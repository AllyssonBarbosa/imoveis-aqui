from django.urls import path

from .views import CidadesPublicasAPIView, EmpresaPublicaAPIView

urlpatterns = [
    path("empresas/<int:pk>/", EmpresaPublicaAPIView.as_view(), name="publico-empresa"),
    path("cidades/", CidadesPublicasAPIView.as_view(), name="publico-cidades"),
]
