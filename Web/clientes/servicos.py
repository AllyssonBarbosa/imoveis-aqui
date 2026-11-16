"""Consultas de cliente usadas pelo painel e pela API (W12).

Fica num lugar só para que as duas telas sigam a mesma regra: sempre
filtrar pela empresa do usuário logado antes de qualquer outra coisa.
"""

from django.db.models import Q

from core.validators import somente_digitos

from .models import Cliente


def clientes_da_empresa(usuario):
    """Clientes da empresa do usuário. Sem empresa (administrador): nenhum."""
    if not usuario.is_authenticated or usuario.empresa_id is None:
        return Cliente.objects.none()
    return Cliente.objects.filter(empresa_id=usuario.empresa_id)


def buscar_clientes(queryset, busca):
    """Busca por nome ou documento (com ou sem pontuação)."""
    busca = (busca or "").strip()
    if not busca:
        return queryset
    filtro = Q(nome_razao_social__icontains=busca)
    documento = somente_digitos(busca)
    if documento:
        filtro |= Q(documento__icontains=documento)
    return queryset.filter(filtro)
