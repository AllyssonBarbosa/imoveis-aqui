from django.urls import path

from .views import EmpresaPublicaAPIView

urlpatterns = [
    path("empresas/<int:pk>/", EmpresaPublicaAPIView.as_view(), name="publico-empresa"),
]
