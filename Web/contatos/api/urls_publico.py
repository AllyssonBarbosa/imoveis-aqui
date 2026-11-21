from django.urls import path

from .views import ContatoPublicoAPIView

urlpatterns = [
    path("imoveis/<int:pk>/contato/", ContatoPublicoAPIView.as_view(), name="publico-imovel-contato"),
]
