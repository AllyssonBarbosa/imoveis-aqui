from django.urls import path

from .views import ImovelVitrineDetalheAPIView, VitrineCidadeAPIView

urlpatterns = [
    path(
        "cidades/<int:cidade_id>/imoveis/", VitrineCidadeAPIView.as_view(), name="publico-vitrine-cidade"
    ),
    path("imoveis/<int:pk>/", ImovelVitrineDetalheAPIView.as_view(), name="publico-imovel"),
]
