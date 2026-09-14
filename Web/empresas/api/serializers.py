from rest_framework import serializers

from contas.api.serializers import CorretorPublicoSerializer
from contas.models import Usuario
from localizacao.models import Cidade

from ..models import Empresa


class CidadeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cidade
        fields = ["id", "nome", "uf"]


class EmpresaPublicaSerializer(serializers.ModelSerializer):
    logomarca = serializers.SerializerMethodField()
    cidades_atuacao = CidadeSerializer(many=True, read_only=True)
    corretores_ativos = serializers.SerializerMethodField()

    class Meta:
        model = Empresa
        fields = [
            "id", "tipo", "razao_social_ou_nome", "creci", "telefone", "whatsapp",
            "email", "descricao", "logomarca", "cidades_atuacao", "corretores_ativos",
        ]

    def get_logomarca(self, empresa):
        if not empresa.logomarca:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(empresa.logomarca.url) if request else empresa.logomarca.url

    def get_corretores_ativos(self, empresa):
        corretores = empresa.usuarios.filter(perfil=Usuario.Perfil.CORRETOR, is_active=True)
        return CorretorPublicoSerializer(corretores, many=True, context=self.context).data
