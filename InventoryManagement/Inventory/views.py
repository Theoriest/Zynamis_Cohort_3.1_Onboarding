"""API views for the Inventory Management system.

Views are split by role so the URLs can be mounted on isolated paths:

- Auth / profile        : open registration + self profile.
- CategoryViewSet       : managers maintain, everyone reads.
- ResourceViewSet       : resource management (Admin / Resource Manager).
- ResourceBrowseViewSet : employees browse available resources.
- BookingViewSet        : employees create / view / cancel their bookings.
- BookingApprovalViewSet: managers view, approve and reject requests.

Pagination, filtering and search are applied on the list endpoints.
Approval reserves stock inside a transaction, following the FIFO decision:
the request approved first consumes availability, so a later overlapping
request can no longer be approved once stock is exhausted.
"""

from django.db import transaction
from django.db.models import Q

from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Booking, Category, Resource, User
from .permissions import (
    IsAdminOrResourceManager,
    IsEmployee,
    IsOwnerOrStaff,
    ReadOnlyOrAdminOrResourceManager,
)
from .serializers import (
    BookingDetailSerializer,
    BookingSerializer,
    CategorySerializer,
    RejectionSerializer,
    ResourceBrowseSerializer,
    ResourceSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
)


class StandardResultsSetPagination(PageNumberPagination):
    """Shared pagination for resource and booking lists."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


# ---------------------------------------------------------------------------
# Users / authentication
#
# Token acquisition (login / refresh) is handled by the JWT library's own
# views wired up in urls.py. These views cover registration and profile.
# ---------------------------------------------------------------------------
class RegisterView(generics.CreateAPIView):
    """Open endpoint: register a new (Employee) account."""

    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]


class ProfileView(generics.RetrieveUpdateAPIView):
    """View / update the authenticated user's own profile."""

    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
class CategoryViewSet(viewsets.ModelViewSet):
    """Managers maintain categories; any authenticated user may read them."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [ReadOnlyOrAdminOrResourceManager]
    pagination_class = StandardResultsSetPagination


class ResourceViewSet(viewsets.ModelViewSet):
    """Resource management for Admins and Resource Managers.

    Supports create / update / delete plus managing quantities, categorising
    and (de)activating. Deletion is exposed, but ``deactivate`` is preferred
    so historical bookings keep their referenced resources (PROTECT).
    """

    queryset = Resource.objects.select_related("category").all()
    serializer_class = ResourceSerializer
    permission_classes = [IsAdminOrResourceManager]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        category = params.get("category")
        if category:
            qs = qs.filter(category_id=category)

        location = params.get("location")
        if location:
            qs = qs.filter(location__icontains=location)

        is_active = params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true", "yes"))

        keyword = params.get("search")
        if keyword:
            qs = qs.filter(
                Q(name__icontains=keyword) | Q(description__icontains=keyword)
            )
        return qs

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """Mark a resource inactive so it can no longer be booked."""
        resource = self.get_object()
        resource.is_active = False
        resource.save(update_fields=["is_active", "date_updated"])
        return Response(self.get_serializer(resource).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Reactivate a previously deactivated resource."""
        resource = self.get_object()
        resource.is_active = True
        resource.save(update_fields=["is_active", "date_updated"])
        return Response(self.get_serializer(resource).data)


class ResourceBrowseViewSet(viewsets.ReadOnlyModelViewSet):
    """Employees browse available resources and view resource details.

    Availability (per the model decisions) means: active, not currently
    booked, already returned, and with stock on hand. A date range may be
    supplied; it is validated (end after start) and reserved for the
    date-aware availability the schema will grow into.
    """

    serializer_class = ResourceBrowseSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        params = self.request.query_params
        qs = Resource.objects.select_related("category").all()

        # Default to only the resources an employee can actually book.
        if params.get("availability", "available").lower() == "available":
            qs = qs.filter(
                is_active=True,
                is_booked=False,
                is_returned=True,
                available_quantity__gt=0,
            )

        category = params.get("category")
        if category:
            qs = qs.filter(category_id=category)

        location = params.get("location")
        if location:
            qs = qs.filter(location__icontains=location)

        keyword = params.get("search")
        if keyword:
            # Portable keyword match. Per the design decision this is where
            # django.contrib.postgres.search (SearchVector/SearchQuery) slots
            # in once the project is on PostgreSQL.
            qs = qs.filter(
                Q(name__icontains=keyword) | Q(description__icontains=keyword)
            )

        self._validate_date_range(params.get("start_date"), params.get("end_date"))
        return qs

    @staticmethod
    def _validate_date_range(start, end):
        if start and end and end <= start:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"end_date": "End date must be after the start date."})


# ---------------------------------------------------------------------------
# Bookings (employees)
# ---------------------------------------------------------------------------
class BookingViewSet(viewsets.ModelViewSet):
    """Employees submit, browse and cancel their own bookings.

    An employee only ever sees their own bookings here; managers use the
    approval views to see everyone's. Cancellation is open for now (business
    rules pending), so an employee may cancel any of their bookings.
    """

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrStaff]
    pagination_class = StandardResultsSetPagination
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action == "create":
            # Only employees may book (no privileged-account bookings).
            return [IsAuthenticated(), IsEmployee()]
        return super().get_permissions()

    def get_queryset(self):
        qs = (
            Booking.objects.select_related("user")
            .prefetch_related("items__resource")
            .filter(user=self.request.user)
        )

        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())
        return qs

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel a pending or approved booking (open while rules are TBD)."""
        booking = self.get_object()
        if booking.status in (Booking.Status.CANCELLED, Booking.Status.COMPLETED):
            return Response(
                {"detail": f"Booking is already {booking.get_status_display().lower()}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # If it was holding stock, return that stock to the pool.
            if booking.status == Booking.Status.APPROVED:
                _release_booking_stock(booking)
            booking.status = Booking.Status.CANCELLED
            booking.save(update_fields=["status", "date_updated"])

        return Response(BookingSerializer(booking, context={"request": request}).data)


# ---------------------------------------------------------------------------
# Booking approval (managers)
# ---------------------------------------------------------------------------
class BookingApprovalViewSet(viewsets.ReadOnlyModelViewSet):
    """Managers view all booking requests and approve or reject them.

    List and detail show the requesting user. Approval reserves stock inside
    a transaction; by the FIFO decision the first approval to exhaust stock
    blocks later overlapping requests.
    """

    serializer_class = BookingDetailSerializer
    permission_classes = [IsAdminOrResourceManager]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = (
            Booking.objects.select_related("user")
            .prefetch_related("items__resource")
            .all()
        )
        params = self.request.query_params

        status_param = params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())

        user_param = params.get("user")
        if user_param:
            qs = qs.filter(user_id=user_param)

        resource_param = params.get("resource")
        if resource_param:
            qs = qs.filter(items__resource_id=resource_param).distinct()

        start_date = params.get("start_date")
        if start_date:
            qs = qs.filter(start_date__gte=start_date)

        end_date = params.get("end_date")
        if end_date:
            qs = qs.filter(end_date__lte=end_date)

        return qs

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Approve a pending request, reserving stock atomically (FIFO)."""
        booking = self.get_object()
        if booking.status != Booking.Status.PENDING:
            return Response(
                {"detail": "Only pending bookings can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Re-check and reserve under a row lock so concurrent approvals
            # cannot both consume the same stock.
            items = booking.items.select_related("resource")
            for item in items:
                resource = Resource.objects.select_for_update().get(pk=item.resource_id)
                if not resource.is_active:
                    return Response(
                        {"detail": f"Resource '{resource.name}' is inactive."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if item.quantity > resource.available_quantity:
                    return Response(
                        {
                            "detail": (
                                f"Only {resource.available_quantity} of "
                                f"'{resource.name}' remain; cannot approve."
                            )
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

            # All items satisfiable -> reserve.
            for item in items:
                resource = Resource.objects.select_for_update().get(pk=item.resource_id)
                resource.available_quantity -= item.quantity
                if resource.available_quantity == 0:
                    resource.is_booked = True
                    resource.is_returned = False
                resource.save(
                    update_fields=[
                        "available_quantity",
                        "is_booked",
                        "is_returned",
                        "date_updated",
                    ]
                )

            booking.status = Booking.Status.APPROVED
            booking.save(update_fields=["status", "date_updated"])

        return Response(self.get_serializer(booking).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Reject a pending request with a required reason."""
        booking = self.get_object()
        if booking.status != Booking.Status.PENDING:
            return Response(
                {"detail": "Only pending bookings can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = RejectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        booking.status = Booking.Status.REJECTED
        booking.rejection_reason = serializer.validated_data["rejection_reason"]
        booking.save(update_fields=["status", "rejection_reason", "date_updated"])
        return Response(self.get_serializer(booking).data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _release_booking_stock(booking):
    """Return a booking's reserved stock to the available pool.

    Used when an approved booking is cancelled: resources become bookable
    again (returned, not booked) so any employee may request them next.
    """
    for item in booking.items.select_related("resource"):
        resource = Resource.objects.select_for_update().get(pk=item.resource_id)
        resource.available_quantity = min(
            resource.total_quantity,
            resource.available_quantity + item.quantity,
        )
        resource.is_booked = False
        resource.is_returned = True
        resource.save(
            update_fields=[
                "available_quantity",
                "is_booked",
                "is_returned",
                "date_updated",
            ]
        )
