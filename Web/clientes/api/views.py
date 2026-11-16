from rest_framework import viewsets

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import PertenceAUmaEmpresa

from ..models import Cliente
from ..servicos import buscar_clientes
from .serializers import ClienteSerializer


class ClienteViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Clientes da própria empresa (gestor e corretor), com ?busca= por nome ou documento.

    Sem DELETE: o cliente fica no histórico dos contratos.
    """

    serializer_class = ClienteSerializer
    permission_classes = [PertenceAUmaEmpresa]
    queryset = Cliente.objects.all()
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        return buscar_clientes(super().get_queryset(), self.request.query_params.get("busca"))
