import type React from 'react';
import { useState, useEffect, useId, useRef } from 'react';
import { useTheme } from 'next-themes';
import { getSentimentLabel, type ReportLanguage } from '../../types/analysis';
import { cn } from '../../utils/cn';
import { normalizeReportLanguage, getReportText } from '../../utils/reportLanguage';

interface ScoreGaugeProps {
  score: number;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
  className?: string;
  language?: ReportLanguage;
}

type SentimentKey = 'greed' | 'neutral' | 'fear';

type GaugeVisualStyle = {
  glowBlur: number;
  glowOpacity: number;
  glowStrokeExtra: number;
  valueTextShadow?: string;
};

/**
 * Sentiment score gauge with an animated glowing ring.
 * Dynamically calculates colors based on sentiment score.
 */
export const ScoreGauge: React.FC<ScoreGaugeProps> = ({
  score,
  size = 'md',
  showLabel = true,
  className = '',
  language = 'zh',
}) => {
  // Animated score state.
  const [animatedScore, setAnimatedScore] = useState(0);
  const [displayScore, setDisplayScore] = useState(0);
  const animationRef = useRef<number | null>(null);
  const prevScoreRef = useRef(0);
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';

  // Animate transitions between score updates.
  useEffect(() => {
    const startScore = prevScoreRef.current;
    const endScore = score;
    const duration = 1000; // Animation duration in ms.
    const startTime = performance.now();

    const animate = (currentTime: number) => {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);

      // Use an ease-out cubic curve for a smoother finish.
      const easeOut = 1 - Math.pow(1 - progress, 3);

      const currentScore = startScore + (endScore - startScore) * easeOut;
      setAnimatedScore(currentScore);
      setDisplayScore(Math.round(currentScore));

      if (progress < 1) {
        animationRef.current = requestAnimationFrame(animate);
      } else {
        prevScoreRef.current = endScore;
      }
    };

    animationRef.current = requestAnimationFrame(animate);

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [score]);

  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);
  const label = getSentimentLabel(score, reportLanguage);

  // Size configuration for each gauge variant.
  const sizeConfig = {
    sm: { width: 100, stroke: 8, fontSize: 'text-2xl', labelSize: 'text-xs' },
    md: { width: 140, stroke: 10, fontSize: 'text-4xl', labelSize: 'text-sm' },
    lg: { width: 180, stroke: 12, fontSize: 'text-5xl', labelSize: 'text-base' },
  };

  const { width, stroke, fontSize, labelSize } = sizeConfig[size];
  const radius = (width - stroke) / 2;
  const circumference = 2 * Math.PI * radius;

  // 轨道（未点亮的那 3/4 圈）画成一条凹槽而不是与程序弧等宽的实心管：
  // 等宽时两条等重的管各自成块，读起来是一条灰色的「进度条弯成圈」；收窄并共用
  // 同一条中心线后，程序弧压在凹槽上面、只从两侧各露出一点，未点亮的部分读作
  // 「还没被填满」，而不是「另一条更暗的进度」。收得再窄（试过 0.45）会细成一根
  // 游丝、跟 12px 的弧接不上，0.55 是还能读出「凹槽」的最窄一档。
  const trackStroke = Math.max(3, stroke * 0.55);

  // Start from the top and render a 270-degree arc.
  const arcLength = circumference * 0.75;
  const progress = (animatedScore / 100) * arcLength;

  // Sentiment colors - dynamically computed based on score thresholds.
  // Light theme uses a restrained glow; dark theme keeps the stronger terminal-style glow.
  const sentimentConfig = {
    greed: {
      color: '#00d4ff',       // Cyan
      glowFilter: 'rgba(0, 212, 255, 0.66)',
      lightColor: '#22d3ee',  // Lighter cyan
      lightEndColor: '#0891b2', // Darker cyan
    },
    neutral: {
      color: '#a855f7',       // Purple
      glowFilter: 'rgba(168, 85, 247, 0.66)',
      lightColor: '#c084fc',  // Lighter purple
      lightEndColor: '#9333ea', // Darker purple
    },
    fear: {
      color: '#ff4466',       // Red
      glowFilter: 'rgba(255, 68, 102, 0.66)',
      lightColor: '#fb7185',  // Lighter rose
      lightEndColor: '#e11d48', // Darker rose
    },
  };

  // Map score to sentiment key
  const getSentimentKey = (s: number): SentimentKey => {
    if (s >= 60) return 'greed';
    if (s >= 40) return 'neutral';
    return 'fear';
  };

  const sentimentKey = getSentimentKey(animatedScore);
  const colors = sentimentConfig[sentimentKey];
  // 稳定的 defs id：原先用 `${sentimentKey}-${score}-${animatedScore}` 拼，
  // 数字每变一次（动画期间每帧）整套 <defs> 就换一次 id，浏览器得重建渐变与滤镜
  // 并重新解析 url(#...)。改成 useId（去掉 React 前缀里的冒号，避免进 url() 出问题）。
  const uniqueId = `gauge-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  // 轨道颜色随主题走：暗色加一点白把凹槽提出来，亮色加一点黑把它压下去。
  // 原先写死 `rgba(255,255,255,0.05)`，亮色主题下等于没有轨道。
  const trackColor = isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(15, 23, 42, 0.09)';
  const gaugeTheme: GaugeVisualStyle = isDark
    ? {
        glowBlur: 2.6,
        glowOpacity: 0.6,
        glowStrokeExtra: 4,
        valueTextShadow: `0 0 18px ${colors.glowFilter.replace('0.66', '0.4')}`,
      }
    : {
        glowBlur: 2.6,
        glowOpacity: 0.4,
        glowStrokeExtra: 4,
        valueTextShadow: `0 0 14px ${colors.glowFilter.replace('0.66', '0.18')}`,
      };

  return (
    <div className={cn('flex flex-col items-center', className)}>
      {showLabel && (
        <span className="label-uppercase mb-3 text-secondary-text">
          {text.fearGreedIndex}
        </span>
      )}

      <div className="relative" style={{ width, height: width }}>
        <svg
          className="gauge-ring overflow-visible"
          width={width}
          height={width}
        >
          <defs>
            {/* 渐变沿纵向走（原先是对角线 x1/y1 → x2/y2）：环上最上方最亮、最下方略深，
                读作自上而下的受光；对角渐变落在环上时，弧线的头尾亮度差是随角度乱走的，
                短弧看起来就像「走到一半忽然变淡」。亮色仍保留 lightColor → lightEndColor
                的色相过渡，暗色只降一点透明度。 */}
            <linearGradient id={`${uniqueId}-gradient`} x1="0%" y1="0%" x2="0%" y2="100%">
              {isDark ? (
                <>
                  <stop offset="0%" stopColor={colors.color} stopOpacity="1" />
                  <stop offset="100%" stopColor={colors.color} stopOpacity="0.86" />
                </>
              ) : (
                <>
                  <stop offset="0%" stopColor={colors.lightColor} stopOpacity="1" />
                  <stop offset="100%" stopColor={colors.lightEndColor} stopOpacity="1" />
                </>
              )}
            </linearGradient>

            <filter id={`${uniqueId}-glow`}>
              <feGaussianBlur stdDeviation={gaugeTheme.glowBlur} result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* 轨道：细凹槽，不发光 */}
          <circle
            cx={width / 2}
            cy={width / 2}
            r={radius}
            fill="none"
            stroke={trackColor}
            strokeWidth={trackStroke}
            strokeLinecap="round"
            strokeDasharray={`${arcLength} ${circumference}`}
            transform={`rotate(135 ${width / 2} ${width / 2})`}
          />

          {/* 发光：只挂在程序弧上，仍是同一段 dash。原先整张 svg 还叠了一层 CSS
              `drop-shadow`，那会连灰色轨道和数字一起描上红边、并把所有边缘糊掉。 */}
          <circle
            cx={width / 2}
            cy={width / 2}
            r={radius}
            fill="none"
            stroke={isDark ? colors.color : colors.lightColor}
            strokeWidth={stroke + gaugeTheme.glowStrokeExtra}
            strokeLinecap="round"
            strokeDasharray={`${progress} ${circumference}`}
            transform={`rotate(135 ${width / 2} ${width / 2})`}
            opacity={gaugeTheme.glowOpacity}
            filter={`url(#${uniqueId}-glow)`}
          />

          {/* Progress arc */}
          <circle
            cx={width / 2}
            cy={width / 2}
            r={radius}
            fill="none"
            stroke={`url(#${uniqueId}-gradient)`}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${progress} ${circumference}`}
            transform={`rotate(135 ${width / 2} ${width / 2})`}
          />
        </svg>

        {/* Center value */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className={cn('font-bold', fontSize, isDark ? 'text-white' : 'text-foreground')}
            style={gaugeTheme.valueTextShadow ? { textShadow: gaugeTheme.valueTextShadow } : {}}
          >
            {displayScore}
          </span>
          {showLabel && (
            <span
              className={`${labelSize} font-semibold mt-1`}
              style={{ color: isDark ? colors.color : colors.lightEndColor }}
            >
              {label.toUpperCase()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
