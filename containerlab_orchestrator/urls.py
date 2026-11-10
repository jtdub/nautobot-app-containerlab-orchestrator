"""Django urlpatterns declaration for containerlab_orchestrator app."""

from django.templatetags.static import static
from django.urls import path
from django.views.generic import RedirectView
from nautobot.apps.urls import NautobotUIViewSetRouter

# Uncomment the following line if you have views to import
# from containerlab_orchestrator import views


app_name = "containerlab_orchestrator"
router = NautobotUIViewSetRouter()

# Here is an example of how to register a viewset, you will want to replace views.ContainerlabOrchestratorUIViewSet with your viewset
# router.register("containerlab_orchestrator", views.ContainerlabOrchestratorUIViewSet)


urlpatterns = [
    path("docs/", RedirectView.as_view(url=static("containerlab_orchestrator/docs/index.html")), name="docs"),
]

urlpatterns += router.urls
