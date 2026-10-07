import React, { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';

interface DialogProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

export const Dialog: React.FC<DialogProps> = ({ isOpen, onClose, title, children, footer }) => {
  const titleId = useId();
  const previous = useRef<HTMLElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();

      // Tab trap
      if (e.key === 'Tab' && dialogRef.current) {
        const focusableElements = dialogRef.current.querySelectorAll(
          'a[href], button, textarea, input[type="text"], input[type="radio"], input[type="checkbox"], select, [tabindex]:not([tabindex="-1"])'
        ) as NodeListOf<HTMLElement>;
        
        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === firstElement) {
            e.preventDefault();
            lastElement?.focus();
          }
        } else {
          if (document.activeElement === lastElement) {
            e.preventDefault();
            firstElement?.focus();
          }
        }
      }
    };

    if (isOpen) {
      previous.current = document.activeElement as HTMLElement;
      document.addEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'hidden';
      
      // Focus first element
      setTimeout(() => {
        if (dialogRef.current) {
          const focusableElements = dialogRef.current.querySelectorAll(
            'a[href], button, textarea, input[type="text"], input[type="radio"], input[type="checkbox"], select, [tabindex]:not([tabindex="-1"])'
          ) as NodeListOf<HTMLElement>;
          focusableElements[0]?.focus();
        }
      }, 10);
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'unset';
      previous.current?.focus();
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 sm:p-6" aria-modal="true" role="dialog" aria-labelledby={titleId}>
      <div 
        className="absolute inset-0 bg-[var(--color-overlay)] animate-in fade-in duration-200" 
        onClick={onClose} 
        aria-hidden="true" 
      />
      <div 
        ref={dialogRef}
        className="relative w-full max-w-lg bg-[var(--color-surface)] border border-[var(--color-semantic-unknown)]/30 rounded-[16px] shadow-2xl flex flex-col animate-in zoom-in-95 fade-in duration-200 max-h-[90vh]"
      >
        <div className="flex items-center justify-between p-5 border-b border-[var(--color-semantic-unknown)]/20">
          <h2 id={titleId} className="text-lg font-bold text-[var(--color-text-primary)]">{title}</h2>
          <button 
            onClick={onClose}
            className="p-1.5 rounded-full hover:bg-white/10 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
            aria-label="Close dialog"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 custom-scrollbar text-[var(--color-text-secondary)]">
          {children}
        </div>
        {footer && (
          <div className="p-5 border-t border-[var(--color-semantic-unknown)]/20 bg-[var(--color-surface-raised)] rounded-b-[16px] flex justify-end gap-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};
