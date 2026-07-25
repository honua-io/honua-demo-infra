###############################################################################
# Cost controls — monthly AWS Budget with actual + forecasted alerts.
#
# The demo account's fixed cost crept to ~$140/mo without anyone being
# notified (a dozen interface-endpoint ENIs — see nat-instance.tf). This
# budget is the tripwire so cost drift becomes an email instead of a
# quarter-end surprise. Thresholds: alerts at 100% of the $150 budget
# (actual AND forecasted) and again at 200% ($300, actual and forecasted).
#
# Budgets is a global (us-east-1-hosted) service but the resource is created
# through the regular provider; it is account-wide COST, not filtered to this
# stack's resources — the demo is the only workload in this account, so an
# account-wide budget is the honest number.
###############################################################################

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_amount)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 200
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold                  = 200
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
