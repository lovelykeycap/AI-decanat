#include <cstdint>
#include <memory>
#include <modules/audio_processing/include/audio_processing.h>

#if defined(_WIN32)
#define DECANAT_API extern "C" __declspec(dllexport)
#else
#define DECANAT_API extern "C" __attribute__((visibility("default")))
#endif

struct DecanatAPM {
    rtc::scoped_refptr<webrtc::AudioProcessing> Processor;
    DecanatAPM() : Processor(webrtc::AudioProcessingBuilder().Create()) {
        webrtc::AudioProcessing::Config Config;
        Config.echo_canceller.enabled = true;
        Config.echo_canceller.mobile_mode = false;
        Config.noise_suppression.enabled = true;
        Config.noise_suppression.level = webrtc::AudioProcessing::Config::NoiseSuppression::kHigh;
        Config.high_pass_filter.enabled = true;
        Processor->ApplyConfig(Config);
    }
};

DECANAT_API void* decanat_apm_create() {
    try { return new DecanatAPM(); } catch (...) { return nullptr; }
}

// One 10 ms frame: 480 mono PCM16 render/capture samples at 48 kHz.
// Returns 160 cleaned capture samples at 16 kHz, suitable for the local VAD/ASR.
DECANAT_API int decanat_apm_process(void* Handle, const int16_t* Capture,
                                  const int16_t* Render, int DelayMs, int16_t* Clean) {
    if (!Handle || !Capture || !Render || !Clean) return -1;
    auto& APM = static_cast<DecanatAPM*>(Handle)->Processor;
    int16_t Reverse[480];
    const webrtc::StreamConfig Input(48000, 1), Output(16000, 1);
    int Result = APM->ProcessReverseStream(Render, Input, Input, Reverse);
    if (Result != 0) return Result;
    APM->set_stream_delay_ms(DelayMs);
    return APM->ProcessStream(Capture, Input, Output, Clean);
}

DECANAT_API void decanat_apm_destroy(void* Handle) { delete static_cast<DecanatAPM*>(Handle); }
