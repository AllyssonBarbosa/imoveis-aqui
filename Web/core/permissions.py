from rest_framework.permissions import BasePermission


class PertenceAUmaEmpresa(BasePermission):
    """Libera acesso só a quem tem empresa vinculada (gestor ou corretor).

    O administrador não pertence a nenhuma empresa e não deve enxergar
    dado de acervo — só quem tem empresa passa por essa permissão.
    """

    message = "Este recurso é restrito a usuários vinculados a uma empresa."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.empresa_id is not None
        )
