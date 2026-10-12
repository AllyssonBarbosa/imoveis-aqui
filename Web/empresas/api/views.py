from rest_framework import generics
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny

from localizacao.models import Cidade

from ..models import Empresa
from .serializers import CidadeSerializer, EmpresaPublicaSerializer


class EmpresaPublicaAPIView(generics.RetrieveAPIView):
    """Perfil público da empresa, sem login. Empresa inativa não aparece."""

    permission_classes = [AllowAny]
    serializer_class = EmpresaPublicaSerializer
    queryset = Empresa.objects.filter(ativa=True)


class CidadeCursorPagination(CursorPagination):
    """Página fixa de 50, ordenação estável e não-nula (contrato §4)."""

    page_size = 50
    ordering = ("nome", "uf")
    cursor_query_param = "cursor"


class CidadesPublicasAPIView(generics.ListAPIView):
    """Cidades atendidas (com ao menos uma Empresa ativa atuando), sem login.

    A regra de "atendida" é decidida inteiramente no queryset do servidor
    (contrato §2.3) — nunca por parâmetro vindo do cliente.
    """

    permission_classes = [AllowAny]
    serializer_class = CidadeSerializer
    pagination_class = CidadeCursorPagination
    queryset = Cidade.objects.filter(empresas_atuantes__ativa=True).distinct()
