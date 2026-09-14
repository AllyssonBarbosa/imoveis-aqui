from django.contrib import admin

from .models import Cidade


@admin.register(Cidade)
class CidadeAdmin(admin.ModelAdmin):
    list_display = ("nome", "uf")
    list_filter = ("uf",)
    search_fields = ("nome",)
    ordering = ("nome", "uf")
