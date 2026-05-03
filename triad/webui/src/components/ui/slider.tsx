import * as React from 'react';
import { cn } from '@/lib/utils';

interface SliderProps {
  value: number[];
  max?: number;
  step?: number;
  onValueChange: (value: number[]) => void;
  className?: string;
}

const Slider = React.forwardRef<HTMLInputElement, SliderProps>(
  ({ value, max = 100, step = 1, onValueChange, className }, ref) => {
    return (
      <div className={cn('relative flex w-full touch-none select-none items-center', className)}>
        <input
          ref={ref}
          type="range"
          min={0}
          max={max}
          step={step}
          value={value[0]}
          onChange={(e) => onValueChange([Number(e.target.value)])}
          className="w-full h-1.5 bg-secondary rounded-lg appearance-none cursor-pointer accent-primary"
        />
      </div>
    );
  }
);
Slider.displayName = 'Slider';

export { Slider };
