from rest_framework.routers import DefaultRouter

from .views import CaracteristicaViewSet, FotoImovelViewSet, ImovelViewSet, PredioViewSet

router = DefaultRouter()
router.register("imoveis", ImovelViewSet, basename="imovel")
router.register("predios", PredioViewSet, basename="predio")
router.register("fotos-imovel", FotoImovelViewSet, basename="foto-imovel")
router.register("caracteristicas", CaracteristicaViewSet, basename="caracteristica")

urlpatterns = router.urls
