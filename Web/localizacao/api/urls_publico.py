from django.urls import path

from .views import CidadeAtendidaAPIView

urlpatterns = [
    path("cidades/", CidadeAtendidaAPIView.as_view(), name="publico-cidades"),
]
