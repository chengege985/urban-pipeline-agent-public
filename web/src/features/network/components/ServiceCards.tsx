import type { ServiceObject, ServiceType } from "../../../shared/contracts/domain";
import { ServiceIcon } from "../../../shared/ui/Icons";

interface ServiceCardsProps {
  services: ServiceObject[];
  activeType: ServiceType | "全部";
  onSelect: (type: ServiceType | "全部") => void;
}

export function ServiceCards({ services, activeType, onSelect }: ServiceCardsProps) {
  return (
    <div className="service-grid">
      {services.map((service) => (
        <button
          className={`service-card ${activeType === service.type ? "active" : ""}`}
          key={service.id}
          onClick={() => onSelect(activeType === service.type ? "全部" : service.type)}
        >
          <span className={`service-icon service-${service.type}`}>
            <ServiceIcon type={service.type} size={19} />
          </span>
          <span className="service-copy">
            <strong>{service.name}</strong>
            <small>{service.description}</small>
          </span>
          <span className="service-meta">
            <b>{service.type}</b>
            <em>{service.households > 0 ? `${service.households} 户` : `${service.facilities} 处设施`}</em>
            {service.critical ? <i>关键</i> : null}
          </span>
        </button>
      ))}
    </div>
  );
}
