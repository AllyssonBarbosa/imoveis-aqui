from django.urls import path

from . import views

app_name = "site_publico"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("cidade/<int:cidade_id>/", views.vitrine, name="vitrine"),
    path("imovel/<int:pk>/", views.imovel, name="imovel"),
    path("empresa/<int:pk>/", views.empresa, name="empresa"),
    path("corretor/<int:pk>/", views.corretor, name="corretor"),
]
