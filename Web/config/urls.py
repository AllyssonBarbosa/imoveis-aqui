from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('painel/', include('contas.urls')),
    path('painel/proprietarios/', include('proprietarios.urls')),
    path('painel/imoveis/', include('imoveis.urls')),
    path('api/', include('contas.api.urls')),
    path('api/', include('proprietarios.api.urls')),
    path('api/', include('imoveis.api.urls')),
    path('api/publico/', include('contas.api.urls_publico')),
    path('api/publico/', include('empresas.api.urls_publico')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
