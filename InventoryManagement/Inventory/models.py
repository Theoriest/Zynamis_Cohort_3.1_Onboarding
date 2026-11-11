from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Stores user information and roles.

    Built on django.contrib.auth's AbstractUser so authentication,
    permissions and the admin keep working. Roles drive the role-based
    access control enforced in the views/permission classes:

    - Admin:            full system access.
    - Resource Manager: manages resources and booking approvals.
    - Employee:         browses resources and makes booking requests.
    """
    #Defined roles and set default to employee for security as they have the list view.

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        RESOURCE_MANAGER = "RESOURCE_MANAGER", "Resource Manager"
        EMPLOYEE = "EMPLOYEE", "Employee"

    role = models.CharField(
        max_length=32,
        choices=Role.choices,
        default=Role.EMPLOYEE,
        help_text="Determines which actions and views the user may access.",
    )

    def __str__(self):
        return f"{self.get_username()} ({self.get_role_display()})"


class Category(models.Model):
    """Groups resources (e.g. Laptops, Projectors, Vehicles)."""
    
    # Take the name and description of the category.

    name = models.CharField(max_length = 100, unique = True)
    description = models.TextField(blank = True)

    date_created = models.DateTimeField(auto_now_add = True)
    date_updated = models.DateTimeField(auto_now = True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Resource(models.Model):
    """Stores equipment and inventory details.

    Managed by resource managers. The three boolean flags below implement
    the availability decisions:

    - is_active   : only active resources may be booked.
    - is_booked   : a booked resource is not listed for further booking.
    - is_returned : a resource is only re-listed once it has been returned.
    """

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    #Foreign key from category as a resource belongs to a category.
    category = models.ForeignKey(
        Category,
        on_delete = models.PROTECT,
        related_name = "resources",
    )

    total_quantity = models.PositiveIntegerField(default=0)

    # Maintained consistently alongside approved bookings.
    available_quantity = models.PositiveIntegerField(default=0)

    #open location for now in future a pre determined list to chose from would be better for resource management and search.
    location = models.CharField(max_length=200, blank=True)

    # Status (active/inactive): inactive resources cannot be booked.
    is_active = models.BooleanField(default=True)

    # Booked/not booked: booked resources are not listed for booking.
    is_booked = models.BooleanField(default=False)

    # Returned/not returned: a held resource is only re-listed once returned.
    is_returned = models.BooleanField(default=True)

    """
    Auto adds current date and time from OS (This is a bug).
    Reason being the users time may be inproperly set and lead to inconsistency in the resource store.
    Should be able to have a true source of time.
    Will research these to find solutions but for now its an accepted bug on my end.
    """
    date_created = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                check = models.Q(available_quantity__lte=models.F("total_quantity")),
                name = "available_quantity_lte_total_quantity",
            ),
        ]

    def __str__(self):
        return self.name


class Booking(models.Model):
    """Stores booking requests.

    A booking is always made under an employee's credentials (admins and
    resource managers must use their employee credentials to book, to avoid
    privilege escalation / accidental bookings). Only approved bookings
    reserve resources; requests are processed FIFO by order of creation.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        COMPLETED = "COMPLETED", "Completed"

    # Check who is making a booking to ensure only an employee can make booking by referencing the user as a foreign key.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.CASCADE,
        related_name = "bookings",
    )

    status = models.CharField(
        max_length = 16,
        choices = Status.choices,
        default = Status.PENDING,
    )

    start_date = models.DateField()
    end_date = models.DateField()

    # Populated by a resource manager when a request is rejected.
    rejection_reason = models.TextField(blank = True)

    date_created = models.DateTimeField(auto_now_add = True)
    date_updated = models.DateTimeField(auto_now = True)

    class Meta:

        # FIFO: requests appear in the order they were made.
        ordering = ["date_created"]
        constraints = [
            models.CheckConstraint(
                check = models.Q(end_date__gt= models.F("start_date")),
                name = "booking_end_date_after_start_date",
            ),
        ]

    def __str__(self):
        #Print the Booking state 
        return f"Booking #{self.pk} by {self.user} [{self.get_status_display()}]"


class BookingItem(models.Model):
    """Stores resources and quantities associated with a booking."""
    #Deleting a booking

    booking = models.ForeignKey(
        Booking,
        on_delete = models.CASCADE,
        related_name= "items",
    )
    #Tying a booking to a resource
    resource = models.ForeignKey(
        Resource,
        on_delete = models.PROTECT,
        related_name = "booking_items",
    )

    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields = ["booking", "resource"],
                name = "unique_resource_per_booking",
            ),
            models.CheckConstraint(
                check = models.Q(quantity__gt=0),
                name = "booking_item_quantity_positive",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.resource} (Booking #{self.booking_id})"
