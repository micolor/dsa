import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GenerationBackendStatusPanel } from '../GenerationBackendStatusPanel';
import { ToastHostProvider } from '../../../contexts/ToastHostContext';
import { Toast } from '../../../components/common/toastStore';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import type { GenerationBackendStatusResponse, TestGenerationBackendResponse } from '../../../types/systemConfig';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';

const {
  getGenerationBackendStatus,
  previewGenerationBackendStatus,
  testGenerationBackend,
} = vi.hoisted(() => ({
  getGenerationBackendStatus: vi.fn(),
  previewGenerationBackendStatus: vi.fn(),
  testGenerationBackend: vi.fn(),
}));

vi.mock('../../../api/systemConfig', () => ({
  systemConfigApi: {
    getGenerationBackendStatus: (...args: unknown[]) => getGenerationBackendStatus(...args),
    previewGenerationBackendStatus: (...args: unknown[]) => previewGenerationBackendStatus(...args),
    testGenerationBackend: (...args: unknown[]) => testGenerationBackend(...args),
  },
}));

const localCliStatus: GenerationBackendStatusResponse = {
  primaryBackendId: 'codex_cli',
  fallbackBackendId: null,
  primary: {
    backendId: 'codex_cli',
    backendType: 'local_cli',
    providerId: 'codex_cli',
    available: true,
    healthStatus: 'passed',
    supportsJson: true,
    supportsTools: false,
    supportsStream: true,
    supportsVision: false,
    isPrimary: true,
    fallbackTarget: null,
    maxConcurrency: 1,
    usageAvailable: false,
    lastErrorCode: null,
    lastErrorMessage: null,
  },
  fallback: null,
  backends: [],
};

const smokePassed: TestGenerationBackendResponse = {
  success: true,
  mode: 'json',
  message: '生成后端冒烟测试通过',
  status: localCliStatus.primary,
};

const litellmStatus: GenerationBackendStatusResponse = {
  primaryBackendId: 'litellm',
  fallbackBackendId: null,
  primary: {
    ...localCliStatus.primary,
    backendId: 'litellm',
    backendType: 'litellm',
    providerId: 'litellm',
    supportsTools: true,
    usageAvailable: true,
  },
  fallback: null,
  backends: [],
};

/**
 * 冒烟结果与加载失败的反馈都走全站浮层（`useToastSync`），宿主不在就渲染不出来。
 * 这里按 `App` 的挂法补上宿主，断言仍然走真实出口。
 */
const PanelWithToastHost: React.FC<React.ComponentProps<typeof GenerationBackendStatusPanel>> = (props) => (
  <ToastHostProvider>
    <GenerationBackendStatusPanel {...props} />
  </ToastHostProvider>
);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

describe('GenerationBackendStatusPanel', () => {
  beforeEach(() => {
    Toast.clear();
    window.localStorage.clear();
    getGenerationBackendStatus.mockReset();
    previewGenerationBackendStatus.mockReset();
    testGenerationBackend.mockReset();
    getGenerationBackendStatus.mockResolvedValue(localCliStatus);
    previewGenerationBackendStatus.mockResolvedValue(localCliStatus);
    testGenerationBackend.mockResolvedValue(smokePassed);
  });

  it('loads saved generation backend status without draft items', async () => {
    render(<PanelWithToastHost items={[]} maskToken="******" />);

    await waitFor(() => {
      expect(getGenerationBackendStatus).toHaveBeenCalledTimes(1);
    });
    expect(previewGenerationBackendStatus).not.toHaveBeenCalled();
    expect(await screen.findByText('codex_cli')).toBeInTheDocument();
    expect(screen.getByText('仅生成')).toBeInTheDocument();
    expect(screen.getByText(/本地 CLI 只用于报告和文本生成/)).toBeInTheDocument();
  });

  it('previews unsaved draft generation backend status', async () => {
    render(
      <PanelWithToastHost
        items={[{ key: 'GENERATION_BACKEND', value: 'opencode_cli' }]}
        maskToken="******"
      />,
    );

    await waitFor(() => {
      expect(previewGenerationBackendStatus).toHaveBeenCalledWith({
        items: [{ key: 'GENERATION_BACKEND', value: 'opencode_cli' }],
        maskToken: '******',
      });
    });
    expect(getGenerationBackendStatus).not.toHaveBeenCalled();
  });

  it('runs JSON smoke test with current draft items', async () => {
    render(
      <PanelWithToastHost
        items={[{ key: 'GENERATION_BACKEND', value: 'codex_cli' }]}
        maskToken="******"
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: /JSON 冒烟测试/ }));

    await waitFor(() => {
      expect(testGenerationBackend).toHaveBeenCalledWith({
        mode: 'json',
        items: [{ key: 'GENERATION_BACKEND', value: 'codex_cli' }],
        maskToken: '******',
      });
    });
    expect(await screen.findByText('冒烟测试通过')).toBeInTheDocument();
  });

  it('clears stale smoke result when draft items change', async () => {
    const { rerender } = render(
      <PanelWithToastHost
        items={[{ key: 'GENERATION_BACKEND', value: 'codex_cli' }]}
        maskToken="******"
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: /JSON 冒烟测试/ }));
    expect(await screen.findByText('冒烟测试通过')).toBeInTheDocument();

    rerender(
      <PanelWithToastHost
        items={[{ key: 'GENERATION_BACKEND', value: 'opencode_cli' }]}
        maskToken="******"
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText('冒烟测试通过')).not.toBeInTheDocument();
    });
  });

  it('ignores stale generation backend preview responses', async () => {
    const firstPreview = deferred<GenerationBackendStatusResponse>();
    const secondPreview = deferred<GenerationBackendStatusResponse>();
    previewGenerationBackendStatus
      .mockReturnValueOnce(firstPreview.promise)
      .mockReturnValueOnce(secondPreview.promise);

    const { rerender } = render(
      <PanelWithToastHost
        items={[{ key: 'GENERATION_BACKEND', value: 'codex_cli' }]}
        maskToken="******"
      />,
    );
    await waitFor(() => expect(previewGenerationBackendStatus).toHaveBeenCalledTimes(1));

    rerender(
      <PanelWithToastHost
        items={[{ key: 'GENERATION_BACKEND', value: 'litellm' }]}
        maskToken="******"
      />,
    );
    await waitFor(() => expect(previewGenerationBackendStatus).toHaveBeenCalledTimes(2));

    await act(async () => {
      secondPreview.resolve(litellmStatus);
      await secondPreview.promise;
    });
    expect(await screen.findByText('litellm')).toBeInTheDocument();

    await act(async () => {
      firstPreview.resolve(localCliStatus);
      await firstPreview.promise;
    });

    await waitFor(() => expect(screen.getByText('litellm')).toBeInTheDocument());
    expect(screen.queryByText('codex_cli')).not.toBeInTheDocument();
  });

  it('clears stale status when preview request fails', async () => {
    const { rerender } = render(<PanelWithToastHost items={[]} maskToken="******" />);
    expect(await screen.findByText('codex_cli')).toBeInTheDocument();

    previewGenerationBackendStatus.mockRejectedValueOnce(new Error('validation failed'));
    rerender(
      <PanelWithToastHost
        items={[{ key: 'GENERATION_BACKEND_TIMEOUT_SECONDS', value: 'bad' }]}
        maskToken="******"
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText('codex_cli')).not.toBeInTheDocument();
    });
  });

  it('shows smoke status even when initial status has not loaded', async () => {
    getGenerationBackendStatus.mockReturnValueOnce(new Promise(() => undefined));

    render(<PanelWithToastHost items={[]} maskToken="******" />);

    fireEvent.click(screen.getByRole('button', { name: /JSON 冒烟测试/ }));

    expect(await screen.findByText('冒烟测试通过')).toBeInTheDocument();
    expect(await screen.findByText('codex_cli')).toBeInTheDocument();
  });

  it('renders generation backend status labels in English when UI language is English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');

    render(
      <UiLanguageProvider>
        <PanelWithToastHost items={[]} maskToken="******" />
      </UiLanguageProvider>,
    );

    expect(await screen.findByText('Generation backend status')).toBeInTheDocument();
    expect(screen.getByText('Primary backend')).toBeInTheDocument();
    expect(screen.getByText('Generation only')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /JSON smoke test/ })).toBeInTheDocument();
  });
});
