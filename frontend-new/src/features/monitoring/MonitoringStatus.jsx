import { ALERT_SEVERITIES, ALERT_STATES, DELIVERY_STATES } from "./alertPresentation";
import Icon from "../../components/ui/Icon";

export default function MonitoringStatus({ value, kind = "state" }) {
  const definitions =
    kind === "severity" ? ALERT_SEVERITIES : kind === "delivery" ? DELIVERY_STATES : ALERT_STATES;
  const definition = definitions[value] || { label: value || "未提供", tone: "gray" };
  return (
    <span className={`monitoring-status monitoring-status--${definition.tone}`}>
      {kind === "severity" ? <Icon name="alert" size={12} /> : <i aria-hidden="true" />}
      {definition.label}
    </span>
  );
}
