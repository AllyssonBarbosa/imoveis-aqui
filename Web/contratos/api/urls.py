from rest_framework.routers import DefaultRouter

from .views import ContratoViewSet, DocumentoContratoViewSet

router = DefaultRouter()
router.register("contratos", ContratoViewSet, basename="contrato")
router.register("documentos-contrato", DocumentoContratoViewSet, basename="documento-contrato")

urlpatterns = router.urls
