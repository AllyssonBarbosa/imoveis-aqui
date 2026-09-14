from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import LoginAPIView, UsuarioViewSet

router = DefaultRouter()
router.register("usuarios", UsuarioViewSet, basename="usuario")

urlpatterns = [
    path("auth/login/", LoginAPIView.as_view(), name="api-login"),
] + router.urls
