"""Unit and integration tests for the Inventory Management API.

Coverage maps to the key design decisions:

- Model constraints (end > start, available <= total, positive quantity).
- Role-based access control at the view layer.
- Employee-only bookings (no privileged-account bookings).
- Availability: inactive / over-quantity requests are rejected.
- FIFO approval: the first approvals reserve stock, blocking later ones.
- Rejection requires a reason; sensitive fields are not exposed.
"""

from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APITestCase

from .models import Booking, BookingItem, Category, Resource, User


def make_user(username, role, password="pass12345"):
    user = User(username=username, role=role)
    user.set_password(password)
    user.save()
    return user


class ModelConstraintTests(APITestCase):
    """Unit tests for database-level constraints."""

    def setUp(self):
        self.category = Category.objects.create(name="Navigation")
        self.resource = Resource.objects.create(
            name="Log Pose",
            category=self.category,
            total_quantity=5,
        )
        self.user = make_user("luffy", User.Role.EMPLOYEE)

    def test_booking_end_must_be_after_start(self):
        today = date.today()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Booking.objects.create(
                    user=self.user, start_date=today, end_date=today
                )

    def test_booking_item_quantity_positive(self):
        booking = Booking.objects.create(
            user=self.user,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=1),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BookingItem.objects.create(
                    booking=booking, resource=self.resource, quantity=0
                )


class BaseAPISetUp(APITestCase):
    def setUp(self):
        self.admin = make_user("robin", User.Role.ADMIN)
        self.manager = make_user("nami", User.Role.RESOURCE_MANAGER)
        self.employee = make_user("luffy", User.Role.EMPLOYEE)
        self.other_employee = make_user("zoro", User.Role.EMPLOYEE)

        self.category = Category.objects.create(name="Communication")
        self.resource = Resource.objects.create(
            name="Den Den Mushi",
            category=self.category,
            total_quantity=5,
            is_active=True,
        )
        self.start = date.today() + timedelta(days=1)
        self.end = date.today() + timedelta(days=3)
        self.window = {"start_date": str(self.start), "end_date": str(self.end)}


class RBACTests(BaseAPISetUp):
    """Integration tests for role-based access control."""

    def test_unauthenticated_is_rejected(self):
        resp = self.client.get("/api/resources/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_employee_cannot_manage_resources(self):
        self.client.force_authenticate(self.employee)
        resp = self.client.post(
            "/api/manage/resources/",
            {"name": "X", "category": self.category.id, "total_quantity": 1},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_create_resource(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            "/api/manage/resources/",
            {
                "name": "Spare Pose",
                "category": self.category.id,
                "total_quantity": 2,
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_manager_cannot_make_booking(self):
        """Privileged roles must not book from their own accounts."""
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            "/api/bookings/",
            {
                **self.window,
                "items": [{"resource": self.resource.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class BookingFlowTests(BaseAPISetUp):
    """Integration tests for the booking lifecycle and availability rules."""

    def _book(self, user, quantity):
        self.client.force_authenticate(user)
        return self.client.post(
            "/api/bookings/",
            {
                **self.window,
                "items": [{"resource": self.resource.id, "quantity": quantity}],
            },
            format="json",
        )

    def test_employee_can_create_booking(self):
        resp = self._book(self.employee, 2)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["status"], Booking.Status.PENDING)

    def test_cannot_book_more_than_available(self):
        resp = self._book(self.employee, 99)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_book_inactive_resource(self):
        self.resource.is_active = False
        self.resource.save()
        resp = self._book(self.employee, 1)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_end_date_must_be_after_start(self):
        self.client.force_authenticate(self.employee)
        today = str(date.today())
        resp = self.client.post(
            "/api/bookings/",
            {
                "start_date": today,
                "end_date": today,
                "items": [{"resource": self.resource.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_only_sees_own_bookings(self):
        self._book(self.employee, 1)
        self.client.force_authenticate(self.other_employee)
        resp = self.client.get("/api/bookings/")
        self.assertEqual(resp.data["count"], 0)

    def test_fifo_approval_exhausts_stock(self):
        """All three requests are made first (each valid while nothing is
        reserved), then approved FIFO: Luffy(2) + Zoro(3) consume all 5 units,
        so the third request can no longer be approved (the brief's example)."""
        third = make_user("usopp", User.Role.EMPLOYEE)
        b1 = self._book(self.employee, 2).data["id"]
        b2 = self._book(self.other_employee, 3).data["id"]
        b3 = self._book(third, 1).data["id"]  # valid now; nothing reserved yet

        self.client.force_authenticate(self.manager)
        r1 = self.client.post(f"/api/approvals/bookings/{b1}/approve/")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        r2 = self.client.post(f"/api/approvals/bookings/{b2}/approve/")
        self.assertEqual(r2.status_code, status.HTTP_200_OK)

        # All 5 units are reserved across the window -> none free.
        self.assertEqual(self.resource.available_quantity(self.start, self.end), 0)

        # Stock is now exhausted, so the third approval is refused (FIFO).
        r3 = self.client.post(f"/api/approvals/bookings/{b3}/approve/")
        self.assertEqual(r3.status_code, status.HTTP_409_CONFLICT)

    def test_rejection_requires_reason(self):
        bid = self._book(self.employee, 1).data["id"]
        self.client.force_authenticate(self.manager)
        resp = self.client.post(f"/api/approvals/bookings/{bid}/reject/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.post(
            f"/api/approvals/bookings/{bid}/reject/",
            {"rejection_reason": "Needed for the log pose run."},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], Booking.Status.REJECTED)

    def test_cancel_approved_returns_stock(self):
        bid = self._book(self.employee, 2).data["id"]
        self.client.force_authenticate(self.manager)
        self.client.post(f"/api/approvals/bookings/{bid}/approve/")
        self.assertEqual(self.resource.available_quantity(self.start, self.end), 3)

        self.client.force_authenticate(self.employee)
        resp = self.client.post(f"/api/bookings/{bid}/cancel/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Cancelling drops it from the reservation set -> all units free again.
        self.assertEqual(self.resource.available_quantity(self.start, self.end), 5)

    def test_non_overlapping_dates_reuse_full_stock(self):
        """The real win of date-aware availability: a resource fully booked for
        one period is still fully available for a non-overlapping period."""
        # Approve a booking that takes all 5 units for the base window.
        bid = self._book(self.employee, 5).data["id"]
        self.client.force_authenticate(self.manager)
        self.assertEqual(
            self.client.post(f"/api/approvals/bookings/{bid}/approve/").status_code,
            status.HTTP_200_OK,
        )
        # A later, non-overlapping window still sees the full 5 units free...
        later_start = self.end + timedelta(days=1)
        later_end = self.end + timedelta(days=3)
        self.assertEqual(
            self.resource.available_quantity(later_start, later_end), 5
        )
        # ...and the same window is now fully reserved.
        self.assertEqual(self.resource.available_quantity(self.start, self.end), 0)


class AuthTests(BaseAPISetUp):
    def test_register_creates_employee_only(self):
        resp = self.client.post(
            "/api/auth/register/",
            {"username": "newbie", "password": "secret12345"},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("password", resp.data)  # sensitive field not exposed
        self.assertEqual(User.objects.get(username="newbie").role, User.Role.EMPLOYEE)

    def test_profile_role_is_read_only(self):
        self.client.force_authenticate(self.employee)
        resp = self.client.patch(
            "/api/auth/profile/", {"role": User.Role.ADMIN, "first_name": "Cap"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.role, User.Role.EMPLOYEE)  # unchanged
        self.assertEqual(self.employee.first_name, "Cap")
