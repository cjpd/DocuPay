from rest_framework.routers import DefaultRouter

from .views import (
    DocumentViewSet,
    ExtractedDataViewSet,
    ReviewTaskViewSet,
    WebhookConfigViewSet,
    WebhookDeliveryLogViewSet,
)

router = DefaultRouter()
router.register(r"extracted", ExtractedDataViewSet, basename="extracted-data")
router.register(r"reviews", ReviewTaskViewSet, basename="review-task")
router.register(r"webhooks", WebhookConfigViewSet, basename="webhook-config")
router.register(r"webhook-deliveries", WebhookDeliveryLogViewSet, basename="webhook-delivery-log")
# Last: the empty prefix's "<pk>/" route would otherwise capture "reviews/" and the other prefixes.
router.register(r"", DocumentViewSet, basename="document")

urlpatterns = router.urls
