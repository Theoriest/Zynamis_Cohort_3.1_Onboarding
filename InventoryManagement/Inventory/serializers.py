"""Serializers for the Inventory Management API.

Serializers also carry the input validation decisions:

- Self-registration always creates an Employee (role is never client-set),
  matching the "default to employee for security" decision.
- Booking validation enforces end_date > start_date, rejects inactive
  resources, and prevents requesting more than is available.
- Sensitive fields (passwords, permission flags) are never exposed.
"""

from django.db import transaction

from rest_framework import serializers

from .models import Booking, BookingItem, Category, Resource, User


# ---------------------------------------------------------------------------
# Users / authentication
# ---------------------------------------------------------------------------

class UserRegistrationSerializer(serializers.ModelSerializer):
    """Registers a new user.

    Role is intentionally not accepted from the client: every self-registered
    account is an Employee. Elevating an account is an admin-only action.
    """

    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "password"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.role = User.Role.EMPLOYEE
        user.set_password(password)
        user.save()
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    """View / update the authenticated user's own profile.

    Role and account flags are read-only so a user cannot escalate their own
    privileges through the profile endpoint.
    """

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
        ]
        read_only_fields = ["id", "username", "role"]


class RequestingUserSerializer(serializers.ModelSerializer):
    """Minimal user details shown to managers on a booking (the requester)."""

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name"]


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description", "date_created", "date_updated"]
        read_only_fields = ["id", "date_created", "date_updated"]


class ResourceSerializer(serializers.ModelSerializer):
    """Full resource representation for management (Admin / Resource Manager)."""

    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Resource
        fields = [
            "id",
            "name",
            "description",
            "category",
            "category_name",
            "total_quantity",
            "location",
            "is_active",
            "date_created",
            "date_updated",
        ]
        read_only_fields = ["id", "date_created", "date_updated"]


class ResourceBrowseSerializer(serializers.ModelSerializer):
    """Read-only resource view for employees browsing the catalogue.

    ``available`` is the number of units free for the requested date range
    (derived from approved bookings). When no range is supplied it reports the
    total quantity. Internal reservation state is never exposed directly.
    """

    category_name = serializers.CharField(source="category.name", read_only=True)
    available = serializers.SerializerMethodField()

    class Meta:
        model = Resource
        fields = [
            "id",
            "name",
            "description",
            "category",
            "category_name",
            "total_quantity",
            "available",
            "location",
            "is_active",
        ]
        read_only_fields = fields

    def get_available(self, obj) -> int:
        # Prefer the queryset annotation (set by the view for a date range);
        # otherwise fall back to the full stock.
        return getattr(obj, "available", obj.total_quantity)


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

class BookingItemSerializer(serializers.ModelSerializer):
    resource_name = serializers.CharField(source="resource.name", read_only=True)

    class Meta:
        model = BookingItem
        fields = ["id", "resource", "resource_name", "quantity"]
        read_only_fields = ["id"]

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        return value


class BookingSerializer(serializers.ModelSerializer):
    """Create and represent a booking with its nested items.

    The requesting user is taken from the request, never the payload.
    """

    items = BookingItemSerializer(many=True)

    class Meta:
        model = Booking
        fields = [
            "id",
            "user",
            "status",
            "start_date",
            "end_date",
            "rejection_reason",
            "items",
            "date_created",
            "date_updated",
        ]
        read_only_fields = [
            "id",
            "user",
            "status",
            "rejection_reason",
            "date_created",
            "date_updated",
        ]

    def validate(self, attrs):
        start = attrs.get("start_date")
        end = attrs.get("end_date")
        if start and end and end <= start:
            raise serializers.ValidationError(
                {"end_date": "End date must be after the start date."}
            )

        items = attrs.get("items", [])
        if not items:
            raise serializers.ValidationError(
                {"items": "A booking must contain at least one resource."}
            )

        # Prevent booking inactive resources, or more than is free for the
        # requested period (availability derived from approved bookings).
        for item in items:
            resource = item["resource"]
            quantity = item["quantity"]
            if not resource.is_active:
                raise serializers.ValidationError(
                    {"items": f"Resource '{resource.name}' is inactive and cannot be booked."}
                )
            available = resource.available_quantity(start, end)
            if quantity > available:
                raise serializers.ValidationError(
                    {
                        "items": (
                            f"Only {available} of '{resource.name}' are "
                            f"available for {start} to {end}."
                        )
                    }
                )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items")
        booking = Booking.objects.create(
            user=self.context["request"].user,
            **validated_data,
        )
        BookingItem.objects.bulk_create(
            [BookingItem(booking=booking, **item) for item in items]
        )
        return booking


class BookingDetailSerializer(BookingSerializer):
    """Booking representation for managers: includes the requesting user."""

    user = RequestingUserSerializer(read_only=True)


class RejectionSerializer(serializers.Serializer):
    """Validates the reason supplied when a manager rejects a booking."""

    rejection_reason = serializers.CharField(
        max_length=1000,
        allow_blank=False,
        help_text="Required explanation shown to the requesting employee.",
    )
