from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import EhGestor, PertenceAUmaEmpresa

from ..arquivos import responder_download
from ..models import Contrato, DocumentoContrato
from ..servicos import assinar_contrato, contratos_visiveis_para
from .serializers import AssinaturaSerializer, ContratoSerializer, DocumentoContratoSerializer, erros_para_api


class ContratoViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Contratos da empresa, com as parcelas.

    - leitura: gestor (todos da empresa) e corretor (só os dele);
    - cadastro, edição e assinatura (POST /contratos/<id>/assinar/): só o gestor;
    - sem DELETE.
    Contrato de outra empresa (ou de outro corretor) cai em 404.
    """

    serializer_class = ContratoSerializer
    queryset = Contrato.objects.select_related("imovel", "cliente", "corretor").prefetch_related(
        "parcelas", "documentos__enviado_por"
    )
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

    @action(detail=True, methods=["post"])
    def assinar(self, request, pk=None):
        contrato = self.get_object()  # já filtrado pela empresa: de fora cai em 404
        entrada = AssinaturaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        try:
            contrato = assinar_contrato(contrato, request.user, entrada.validated_data["data_assinatura"])
        except DjangoValidationError as erro:
            raise erros_para_api(erro)
        return Response(self.get_serializer(contrato).data)


class DocumentoContratoViewSet(
    EmpresaScopedQuerySetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Documentos do contrato (?contrato=<id>). Arquivo privado: sai só pela ação `baixar`.

    - listar, ver e baixar: gestor (empresa) e corretor (só dos contratos dele);
    - enviar (multipart) e excluir: só o gestor.
    """

    serializer_class = DocumentoContratoSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = DocumentoContrato.objects.select_related("enviado_por")

    def get_permissions(self):
        if self.action in ("list", "retrieve", "baixar"):
            return [PertenceAUmaEmpresa()]
        return [EhGestor()]

    def get_queryset(self):
        # empresa (mixin) + só documentos de contratos que o usuário pode ver
        queryset = super().get_queryset().filter(contrato__in=contratos_visiveis_para(self.request.user))
        contrato = self.request.query_params.get("contrato")
        if contrato and contrato.isdigit():
            queryset = queryset.filter(contrato_id=int(contrato))
        return queryset

    def perform_create(self, serializer):
        contrato = serializer.validated_data["contrato"]
        serializer.save(empresa_id=contrato.empresa_id, enviado_por=self.request.user)

    def perform_destroy(self, documento):
        documento.arquivo.delete(save=False)  # apaga o arquivo do disco, não só o registro
        documento.delete()

    @action(detail=True, methods=["get"])
    def baixar(self, request, pk=None):
        documento = self.get_object()  # já filtrado pela empresa e pelo perfil
        return responder_download(documento.arquivo, documento.nome_para_download)
