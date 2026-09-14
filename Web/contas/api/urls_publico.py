from django.urls import path

from .views import CorretorPublicoAPIView

urlpatterns = [
    path("corretores/<int:pk>/", CorretorPublicoAPIView.as_view(), name="publico-corretor"),
]
