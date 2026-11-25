import type { ReactNode } from "react";
import { InfoIcon } from "./Icons";

interface EmptyStateProps {
  title: string;
  message: string;
  action?: ReactNode;
}

export function EmptyState({ title, message, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <InfoIcon size={22} />
      </span>
      <strong>{title}</strong>
      <p>{message}</p>
      {action}
    </div>
  );
}
