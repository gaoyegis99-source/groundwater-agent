import { useId, type ComponentPropsWithRef, type ReactNode } from 'react';

type FieldProps = {
  label: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
};

function useField({ id, describedBy, description, error }: {
  id?: string; describedBy?: string; description?: ReactNode; error?: ReactNode;
}) {
  const generated = useId();
  const fieldId = id ?? generated;
  const descriptionId = description ? `${fieldId}-hint` : undefined;
  const errorId = error ? `${fieldId}-error` : undefined;
  return {
    fieldId,
    descriptionId,
    errorId,
    describedBy: [describedBy, descriptionId, errorId].filter(Boolean).join(' ') || undefined,
  };
}

type FieldFrameProps = FieldProps & {
  fieldId: string;
  descriptionId?: string;
  errorId?: string;
  unit?: string;
  children: ReactNode;
};

function FieldFrame({ fieldId, descriptionId, errorId, label, description, error, unit, children }: FieldFrameProps) {
  return <div className="gw-field">
    <label className="gw-field-label" htmlFor={fieldId}>{label}</label>
    <div className="gw-field-control">{children}{unit && <span className="gw-field-unit" id={`${fieldId}-unit`}>{unit}</span>}</div>
    {descriptionId && <div className="gw-field-hint" id={descriptionId}>{description}</div>}
    {errorId && <div className="gw-field-error" id={errorId}>{error}</div>}
  </div>;
}

export function Input({ label, description, error, unit, id, 'aria-describedby': describedBy, 'aria-invalid': invalid, ...props }:
  FieldProps & { unit?: string } & ComponentPropsWithRef<'input'>) {
  const ids = useField({ id, describedBy, description, error });
  const fieldDescription = [unit && `${ids.fieldId}-unit`, ids.describedBy].filter(Boolean).join(' ') || undefined;
  return <FieldFrame {...ids} label={label} description={description} error={error} unit={unit}>
    <input {...props} id={ids.fieldId} className={`gw-input ${props.className ?? ''}`}
      aria-describedby={fieldDescription} aria-invalid={error ? true : invalid} />
  </FieldFrame>;
}

export function Select({ label, description, error, id, 'aria-describedby': describedBy, 'aria-invalid': invalid, ...props }:
  FieldProps & ComponentPropsWithRef<'select'>) {
  const ids = useField({ id, describedBy, description, error });
  return <FieldFrame {...ids} label={label} description={description} error={error}>
    <select {...props} id={ids.fieldId} className={`gw-input ${props.className ?? ''}`}
      aria-describedby={ids.describedBy} aria-invalid={error ? true : invalid} />
  </FieldFrame>;
}
