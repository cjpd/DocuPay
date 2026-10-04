from rest_framework import serializers

from .models import Organization


class OrganizationSerializer(serializers.ModelSerializer):
    # The signed-in user's role here, so the UI shows only the controls they can use.
    my_role = serializers.SerializerMethodField()

    def get_my_role(self, obj):
        request = self.context.get("request")
        if not request:
            return None
        membership = obj.memberships.filter(user=request.user).first()
        return membership.role if membership else None

    class Meta:
        model = Organization
        fields = [
            "id", "name", "slug", "auto_approve_threshold", "auto_approve_max_amount", "review_new_vendors", "my_role",
            "created_at", "updated_at",
        ]

    def validate_auto_approve_threshold(self, value):
        if not 0.5 <= value <= 1.0:
            raise serializers.ValidationError("Use a value between 0.5 and 1.0.")
        return value
