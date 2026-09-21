from rest_framework.routers import DefaultRouter

from .views import ProprietarioViewSet

router = DefaultRouter()
router.register("proprietarios", ProprietarioViewSet, basename="proprietario")

urlpatterns = router.urls
