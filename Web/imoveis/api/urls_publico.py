from django.urls import path

from .views import (
    BairrosDaCidadeAPIView,
    CaracteristicasPublicasAPIView,
    ImovelVitrineDetalheAPIView,
    VitrineCidadeAPIView,
)

urlpatterns = [
    path(
        "cidades/<int:cidade_id>/imoveis/", VitrineCidadeAPIView.as_view(), name="publico-vitrine-cidade"
    ),
    path(
        "cidades/<int:cidade_id>/bairros/",
        BairrosDaCidadeAPIView.as_view(),
        name="publico-bairros-cidade",
    ),
    path(
        "caracteristicas/", CaracteristicasPublicasAPIView.as_view(), name="publico-caracteristicas"
    ),
    path("imoveis/<int:pk>/", ImovelVitrineDetalheAPIView.as_view(), name="publico-imovel"),
]
