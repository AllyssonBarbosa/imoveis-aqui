from rest_framework.routers import DefaultRouter

from .views import BaixaViewSet, ContratoViewSet, DocumentoContratoViewSet

router = DefaultRouter()
router.register("contratos", ContratoViewSet, basename="contrato")
router.register("documentos-contrato", DocumentoContratoViewSet, basename="documento-contrato")
router.register("baixas", BaixaViewSet, basename="baixa")

urlpatterns = router.urls
