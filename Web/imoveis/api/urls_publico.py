from rest_framework.routers import SimpleRouter

from .views import VitrineImovelViewSet

router = SimpleRouter()
router.register("imoveis", VitrineImovelViewSet, basename="vitrine-imovel")

urlpatterns = router.urls
