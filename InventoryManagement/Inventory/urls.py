"""URL routing for the Inventory Management API.

Routes are grouped by role so the isolation decision holds at the URL level:

- Public / auth : register, profile, JWT login + refresh.
- Catalogue     : categories, employee resource browsing.
- Management    : /manage/resources  (Admin / Resource Manager).
- Bookings      : /bookings          (employees, own bookings).
- Approvals     : /approvals/bookings (Admin / Resource Manager).

Token acquisition uses the JWT library's views (SimpleJWT); install it and
add the DRF auth settings as noted, then these routes provide login.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .views import (
    BookingApprovalViewSet,
    BookingViewSet,
    CategoryViewSet,
    ProfileView,
    RegisterView,
    ResourceBrowseViewSet,
    ResourceViewSet,
)

app_name = "inventory"

router = DefaultRouter()
# Catalogue
router.register("categories", CategoryViewSet, basename="category")
router.register("resources", ResourceBrowseViewSet, basename="resource-browse")
# Resource management (Admin / Resource Manager)
router.register("manage/resources", ResourceViewSet, basename="resource-manage")
# Bookings (employees)
router.register("bookings", BookingViewSet, basename="booking")
# Booking approval (Admin / Resource Manager)
router.register("approvals/bookings", BookingApprovalViewSet, basename="booking-approval")

urlpatterns = [
    # Authentication
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/profile/", ProfileView.as_view(), name="profile"),
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # API resources
    path("", include(router.urls)),
]
