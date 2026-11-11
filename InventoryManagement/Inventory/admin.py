"""Django admin registrations for the Inventory Management models.

The admin gives Admin users full oversight (the "full system access" role)
and is where staff / superuser flags and the role field are managed. API
access control lives in the permission classes; this is the back-office view.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Booking, BookingItem, Category, Resource, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Extends the stock UserAdmin to surface and manage the role field."""

    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = BaseUserAdmin.list_filter + ("role",)
    search_fields = ("username", "email", "first_name", "last_name")

    # Add the role to the existing fieldsets (edit and create screens).
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Role", {"fields": ("role",)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Role", {"fields": ("role",)}),
    )


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "date_created")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "total_quantity",
        "location",
        "is_active",
    )
    list_filter = ("is_active", "category")
    search_fields = ("name", "description", "location")
    list_select_related = ("category",)
    ordering = ("name",)


class BookingItemInline(admin.TabularInline):
    """Edit a booking's resources / quantities alongside the booking."""

    model = BookingItem
    extra = 0
    autocomplete_fields = ("resource",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "status",
        "start_date",
        "end_date",
        "date_created",
    )
    list_filter = ("status", "start_date", "end_date")
    search_fields = ("user__username", "user__email")
    list_select_related = ("user",)
    date_hierarchy = "date_created"
    inlines = [BookingItemInline]
    readonly_fields = ("date_created", "date_updated")


@admin.register(BookingItem)
class BookingItemAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "resource", "quantity")
    search_fields = ("booking__id", "resource__name")
    list_select_related = ("booking", "resource")
