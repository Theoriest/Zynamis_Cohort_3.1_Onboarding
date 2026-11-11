"""Role-based access control for the Inventory Management API.

These permission classes implement the role decisions:

- Admin:            full access to every view.
- Resource Manager: resource management + booking approval only.
- Employee:         browse resources and make / manage their own bookings(This allows them to delete).

Bookings are deliberately employee-only so that admins and resource
managers cannot book from their privileged accounts (they must use their
employee credentials), avoiding privilege escalation and accidental
bookings.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import User


class IsAdmin(BasePermission):
    """Allows access only to Admin users (full system access)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role == User.Role.ADMIN
        )


class IsResourceManager(BasePermission):
    """Allows access only to Resource Manager users."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role == User.Role.RESOURCE_MANAGER
        )


class IsEmployee(BasePermission):
    """Allows access only to Employee users.

    Used to lock booking creation to employees so privileged roles cannot
    book from their admin / manager accounts.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role == User.Role.EMPLOYEE
        )


class IsAdminOrResourceManager(BasePermission):
    """Resource management and booking approval: Admin or Resource Manager."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in (User.Role.ADMIN, User.Role.RESOURCE_MANAGER)
        )


class ReadOnlyOrAdminOrResourceManager(BasePermission):
    """Anyone authenticated may read; only Admin / Resource Manager may write.

    Handy for shared resources such as Category, which employees browse but
    only managers maintain.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return user.role in (User.Role.ADMIN, User.Role.RESOURCE_MANAGER)


class IsOwnerOrStaff(BasePermission):
    """Object-level: the owning employee, or any Admin / Resource Manager.

    Lets an employee see / act on their own bookings while admins and
    resource managers can view any booking (e.g. history per employee).
    """

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role in (User.Role.ADMIN, User.Role.RESOURCE_MANAGER):
            return True
        owner = getattr(obj, "user", None)
        return owner == user
