from rest_framework import serializers

from ..models import Contato


class NovoContatoSerializer(serializers.Serializer):
    """Corpo do POST público: só o canal. Nenhum dado do visitante é aceito."""

    canal = serializers.ChoiceField(choices=Contato.Canal.choices)


class ContatoSerializer(serializers.ModelSerializer):
    """Caixa de contatos (corretor/gestor logado). Só o retorno pode ser alterado.

    Aqui o código do imóvel aparece: é a API interna da empresa, não a vitrine.
    """

    imovel_codigo = serializers.CharField(source="imovel.codigo", read_only=True)
    imovel_titulo = serializers.CharField(source="imovel.titulo", read_only=True)
    corretor_nome = serializers.CharField(source="corretor.nome", read_only=True, default=None)
    canal_exibicao = serializers.CharField(source="get_canal_display", read_only=True)
    retorno_exibicao = serializers.CharField(source="get_retorno_display", read_only=True)
    retorno = serializers.ChoiceField(choices=Contato.RETORNOS_ANOTAVEIS)

    class Meta:
        model = Contato
        fields = [
            "id", "imovel", "imovel_codigo", "imovel_titulo", "corretor", "corretor_nome",
            "canal", "canal_exibicao", "criado_em", "retorno", "retorno_exibicao", "retorno_em",
        ]
        read_only_fields = ["id", "imovel", "corretor", "canal", "criado_em", "retorno_em"]

    def validate(self, atributos):
        # no PATCH parcial o campo não é obrigatório e o `update` levantaria KeyError (500):
        # sem `retorno` não há o que anotar — emenda ao D-25
        if "retorno" not in atributos:
            raise serializers.ValidationError({"retorno": ["Este campo é obrigatório."]})
        return atributos

    def update(self, instance, validated_data):
        instance.anotar_retorno(validated_data["retorno"])
        return instance
