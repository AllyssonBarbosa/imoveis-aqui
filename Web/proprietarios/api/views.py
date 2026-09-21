from django.db.models import Q
from rest_framework import viewsets

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import EhCorretor
from core.validators import somente_digitos

from proprietarios.models import Proprietario

from .serializers import ProprietarioSerializer


class ProprietarioViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """CRUD de proprietários da própria empresa, com busca por nome ou documento."""

    serializer_class = ProprietarioSerializer
    permission_classes = [EhCorretor]
    queryset = Proprietario.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        busca = self.request.query_params.get("busca")
        if busca:
            filtro = Q(nome_razao_social__icontains=busca)
            documento_busca = somente_digitos(busca)
            if documento_busca:
                filtro |= Q(documento__icontains=documento_busca)
            queryset = queryset.filter(filtro)
        return queryset
