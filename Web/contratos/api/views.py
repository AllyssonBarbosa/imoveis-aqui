from rest_framework import viewsets

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import EhGestor, PertenceAUmaEmpresa

from ..models import Contrato
from ..servicos import contratos_visiveis_para
from .serializers import ContratoSerializer


class ContratoViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Contratos da empresa, com as parcelas.

    - leitura: gestor (todos da empresa) e corretor (só os dele);
    - cadastro e edição: só o gestor;
    - sem DELETE.
    Contrato de outra empresa (ou de outro corretor) cai em 404.
    """

    serializer_class = ContratoSerializer
    queryset = Contrato.objects.select_related("imovel", "cliente", "corretor").prefetch_related("parcelas")
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [PertenceAUmaEmpresa()]
        return [EhGestor()]

    def get_queryset(self):
        # o mixin já filtra pela empresa; aqui o perfil corta o resto (corretor: só os dele)
        queryset = super().get_queryset().filter(pk__in=contratos_visiveis_para(self.request.user))
        for parametro in ("tipo", "situacao"):
            valor = self.request.query_params.get(parametro)
            if valor:
                queryset = queryset.filter(**{parametro: valor})
        return queryset

    def perform_create(self, serializer):
        # a empresa é definida dentro do serializer (salvar_contrato precisa dela antes de validar)
        serializer.save()
