from rest_framework.routers import DefaultRouter

from .views import (
    CaracteristicaViewSet,
    DocumentoImovelViewSet,
    FotoImovelViewSet,
    GestaoImovelViewSet,
    ImovelViewSet,
    LoteamentoViewSet,
    PredioViewSet,
    QuadraViewSet,
)

router = DefaultRouter()
router.register("imoveis", ImovelViewSet, basename="imovel")
router.register("predios", PredioViewSet, basename="predio")
router.register("loteamentos", LoteamentoViewSet, basename="loteamento")
router.register("quadras", QuadraViewSet, basename="quadra")
router.register("fotos-imovel", FotoImovelViewSet, basename="foto-imovel")
router.register("caracteristicas", CaracteristicaViewSet, basename="caracteristica")
router.register("gestao/imoveis", GestaoImovelViewSet, basename="gestao-imovel")
router.register("gestao/documentos", DocumentoImovelViewSet, basename="gestao-documento")

urlpatterns = router.urls
