from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import EuAPIView, LoginAPIView, SairAPIView, UsuarioViewSet

router = DefaultRouter()
router.register("usuarios", UsuarioViewSet, basename="usuario")

urlpatterns = [
    path("auth/login/", LoginAPIView.as_view(), name="api-login"),
    path("auth/eu/", EuAPIView.as_view(), name="api-eu"),
    path("auth/sair/", SairAPIView.as_view(), name="api-sair"),
] + router.urls
