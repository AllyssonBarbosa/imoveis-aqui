from rest_framework import generics, status, viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import PertenceAUmaEmpresa
from core.throttles import ThrottleLoginPorEmail, ThrottleLoginPorIp

from contas.models import Usuario

from .serializers import CorretorPublicoSerializer, LoginSerializer, UsuarioSerializer


class LoginAPIView(APIView):
    """Devolve um token válido para o usuário autenticado (gestor ou corretor).

    Limita as tentativas por e-mail e por IP (429 com Retry-After) antes de conferir a senha, e a
    resposta do limite é igual para e-mail existente ou não. Não autentica a requisição por
    cabeçalho: um `Authorization: Token <revogado>` mandado ao login não pode derrubá-lo com 401.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ThrottleLoginPorEmail, ThrottleLoginPorIp]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        usuario = serializer.validated_data["usuario"]
        token, _ = Token.objects.get_or_create(user=usuario)
        return Response({"token": token.key})


class EuAPIView(APIView):
    """Diz ao app o papel de quem está logado: `{"perfil": "CORRETOR"|"GESTOR"|"ADMINISTRADOR"}`.

    O app roteia a tela por esse papel (D-02, D-B). Devolve só o perfil de propósito (nenhuma tela
    usa nome nem empresa); por isso não reutiliza o UsuarioSerializer, que expõe e-mail e empresa.
    Só aceita `Authorization: Token`: é rota do app, sem sessão do site nem CSRF.
    """

    authentication_classes = [TokenAuthentication]

    def get(self, request):
        return Response({"perfil": request.user.perfil})


class SairAPIView(APIView):
    """Sair: revoga no servidor a chave do usuário (204).

    O token é único por usuário (o login usa get_or_create), então apagar a chave desconecta todos
    os aparelhos dele; o site, que usa sessão, não é afetado. Um novo login gera chave nova. Repetir
    o Sair com a chave já revogada dá 401 (o app trata como sucesso). Só aceita `Authorization: Token`.
    """

    authentication_classes = [TokenAuthentication]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UsuarioViewSet(EmpresaScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    """Lista os usuários (corretores) da própria empresa — base do isolamento multitenant."""

    serializer_class = UsuarioSerializer
    permission_classes = [PertenceAUmaEmpresa]
    queryset = Usuario.objects.all()


class CorretorPublicoAPIView(generics.RetrieveAPIView):
    """Perfil público do corretor, sem login.

    Corretor desativado ou de empresa inativa não aparece (404) — a mesma regra do site (WEB-09).
    """

    permission_classes = [AllowAny]
    serializer_class = CorretorPublicoSerializer
    queryset = Usuario.objects.filter(
        perfil=Usuario.Perfil.CORRETOR, is_active=True, empresa__ativa=True
    )
