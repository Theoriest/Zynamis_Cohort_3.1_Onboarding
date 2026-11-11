"""Seed an example database themed on the Straw Hat Pirates.

This command is a living demonstration of the design decisions:

- Roles: the crew is split across Admin / Resource Manager / Employee.
- Employee-only bookings: the captain (Luffy) and others book as Employees;
  the Resource Manager and Admin never book from their privileged accounts.
- Date-aware availability: availability is derived from the approved bookings
  that overlap a requested period (total_quantity minus overlapping approvals).
- FIFO approval: the projector-style example from the brief is reproduced with
  Den Den Mushi, so approving earlier requests exhausts stock for later ones.

Run with:  python manage.py seed_strawhats
Add --flush to wipe existing Inventory data first.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from Inventory.models import Booking, BookingItem, Category, Resource, User

PASSWORD = "GomuGomu123"  # demo password for every seeded account

# (username, role, first, last, email)
CREW = [
    ("robin", User.Role.ADMIN, "Nico", "Robin", "robin@sunny.sea"),
    ("nami", User.Role.RESOURCE_MANAGER, "Nami", "", "nami@sunny.sea"),
    ("franky", User.Role.RESOURCE_MANAGER, "Franky", "", "franky@sunny.sea"),
    ("luffy", User.Role.EMPLOYEE, "Monkey D.", "Luffy", "luffy@sunny.sea"),
    ("zoro", User.Role.EMPLOYEE, "Roronoa", "Zoro", "zoro@sunny.sea"),
    ("usopp", User.Role.EMPLOYEE, "Usopp", "", "usopp@sunny.sea"),
    ("sanji", User.Role.EMPLOYEE, "Vinsmoke", "Sanji", "sanji@sunny.sea"),
    ("chopper", User.Role.EMPLOYEE, "Tony Tony", "Chopper", "chopper@sunny.sea"),
    ("brook", User.Role.EMPLOYEE, "Brook", "", "brook@sunny.sea"),
    ("jinbe", User.Role.EMPLOYEE, "Jinbe", "", "jinbe@sunny.sea"),
]

# name -> description
CATEGORIES = {
    "Navigation": "Charts, log poses and weather gear for sailing the Grand Line.",
    "Combat Equipment": "Weapons and gear for crew defence.",
    "Medical": "Chopper's clinic supplies.",
    "Culinary": "Sanji's galley equipment.",
    "Ship Equipment": "Thousand Sunny systems and small craft.",
    "Communication": "Den Den Mushi and signalling gear.",
}

# (name, category, total_quantity, location, is_active)
RESOURCES = [
    ("Log Pose", "Navigation", 6, "Navigation Room", True),
    ("Eternal Pose (Fish-Man Island)", "Navigation", 2, "Navigation Room", True),
    ("Clima-Tact", "Combat Equipment", 1, "Nami's Cabin", True),
    ("Kuro Kabuto Slingshot", "Combat Equipment", 3, "Factory", True),
    ("Rumble Ball Kit", "Medical", 4, "Sick Bay", True),
    ("Emergency Medical Kit", "Medical", 5, "Sick Bay", True),
    ("Galley Knife Set", "Culinary", 2, "Aquarium Bar", True),
    ("Mini Merry II", "Ship Equipment", 1, "Dock System", True),
    ("Cola Barrel (Coup de Burst)", "Ship Equipment", 10, "Soldier Dock", True),
    ("Soul Solid Cane", "Combat Equipment", 1, "Crow's Nest", False),  # inactive demo
    ("Den Den Mushi", "Communication", 5, "Helm", True),  # FIFO example resource
]


class Command(BaseCommand):
    help = "Seed an example Straw Hat Pirates database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing Inventory data before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            BookingItem.objects.all().delete()
            Booking.objects.all().delete()
            Resource.objects.all().delete()
            Category.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.WARNING("Flushed existing Inventory data."))

        users = self._seed_users()
        categories = self._seed_categories()
        resources = self._seed_resources(categories)
        self._seed_bookings(users, resources)

        self.stdout.write(self.style.SUCCESS("\nStraw Hat crew is aboard! Summary:"))
        self.stdout.write(f"  Users:      {User.objects.count()}")
        self.stdout.write(f"  Categories: {Category.objects.count()}")
        self.stdout.write(f"  Resources:  {Resource.objects.count()}")
        self.stdout.write(f"  Bookings:   {Booking.objects.count()}")
        self.stdout.write(
            self.style.HTTP_INFO(f"\n  Every account's password is: {PASSWORD}")
        )

    def _seed_users(self):
        users = {}
        for username, role, first, last, email in CREW:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "role": role,
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                },
            )
            if created:
                user.set_password(PASSWORD)
                user.save()
            users[username] = user
            flag = "created" if created else "exists"
            self.stdout.write(f"  user {username:<8} [{user.get_role_display()}] ({flag})")
        return users

    def _seed_categories(self):
        categories = {}
        for name, description in CATEGORIES.items():
            cat, _ = Category.objects.get_or_create(
                name=name, defaults={"description": description}
            )
            categories[name] = cat
        return categories

    def _seed_resources(self, categories):
        resources = {}
        for name, cat_name, total, location, is_active in RESOURCES:
            res, _ = Resource.objects.get_or_create(
                name=name,
                defaults={
                    "category": categories[cat_name],
                    "description": f"{name} stored at {location}.",
                    "total_quantity": total,
                    "location": location,
                    "is_active": is_active,
                },
            )
            resources[name] = res
        return resources

    def _seed_bookings(self, users, resources):
        """Reproduce the brief's availability example with Den Den Mushi (5 units).

        Luffy books 2, Zoro books 3, Usopp books 4. Approving Luffy and Zoro
        (FIFO) exhausts the 5 units, so Usopp's request can no longer be met.
        """
        if Booking.objects.exists():
            return  # keep idempotent; only seed bookings on a fresh set

        den_den = resources["Den Den Mushi"]
        today = date.today()
        window = (today + timedelta(days=1), today + timedelta(days=3))

        luffy_b = self._make_booking(users["luffy"], den_den, 2, window)
        zoro_b = self._make_booking(users["zoro"], den_den, 3, window)
        self._make_booking(users["usopp"], den_den, 4, window)  # left pending

        # Approve Luffy then Zoro (FIFO), reserving all 5 units.
        self._approve(luffy_b)
        self._approve(zoro_b)

        self.stdout.write(
            self.style.HTTP_INFO(
                "  Den Den Mushi: Luffy(2) + Zoro(3) approved -> 0 left; "
                "Usopp(4) stays pending (cannot be approved)."
            )
        )

    @staticmethod
    def _make_booking(user, resource, quantity, window):
        booking = Booking.objects.create(
            user=user, start_date=window[0], end_date=window[1]
        )
        BookingItem.objects.create(booking=booking, resource=resource, quantity=quantity)
        return booking

    @staticmethod
    def _approve(booking):
        """Approve a booking. Availability is derived from approved bookings,
        so approving simply flips the status -- no counters to maintain."""
        booking.status = Booking.Status.APPROVED
        booking.save()
