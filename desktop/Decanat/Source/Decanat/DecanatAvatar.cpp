#include "DecanatAvatar.h"
#include "DecanatClient.h"
#include "Components/SkeletalMeshComponent.h"
#include "Animation/AnimSequence.h"
#include "Dom/JsonObject.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Base64.h"
#include "TimerManager.h"
#include "Async/Async.h"
#include "Engine/GameInstance.h"
#include "UObject/StrongObjectPtr.h"
#if DECANAT_WITH_ACE
#include "ACERuntimeModule.h"
#include "ACEAudioCurveSourceComponent.h"
#include "ACEBlueprintLibrary.h"
#endif

void ADecanatAvatar::BeginPlay()
{
    Super::BeginPlay();
    Client = GetGameInstance()->GetSubsystem<UDecanatClient>();
    FString ClassPath;
    GConfig->GetString(TEXT("Decanat"), TEXT("AvatarClass"), ClassPath, GGameIni);
    GConfig->GetString(TEXT("Decanat"), TEXT("LocalFaceProvider"), FaceProvider, GGameIni);
    if (ClassPath.IsEmpty()) return;
    UClass* CharacterClass = LoadClass<AActor>(nullptr, *ClassPath);
    if (!CharacterClass) return;
    Character = GetWorld()->SpawnActor<AActor>(CharacterClass, GetActorTransform());
    TArray<USkeletalMeshComponent*> Meshes;
    if (Character) Character->GetComponents(Meshes);
    for (auto* Mesh : Meshes) if (Mesh->GetFName() == FName(TEXT("Body"))) Body = Mesh;
    bConfigured = Body != nullptr;
    for (const FString Key : {TEXT("SitIdleAnimation"), TEXT("StandAnimation"), TEXT("StandIdleAnimation"), TEXT("SitAnimation")}) {
        FString AnimationPath;
        GConfig->GetString(TEXT("Decanat"), *Key, AnimationPath, GGameIni);
        if (AnimationPath.IsEmpty() || !LoadObject<UAnimSequence>(nullptr, *AnimationPath)) bConfigured = false;
    }
#if DECANAT_WITH_ACE
    const TSet<FString> LocalProviders = {
        TEXT("LocalA2F-Mark"), TEXT("LocalA2F-James"), TEXT("LocalA2F-Claire"),
        TEXT("LocalA2F-Mark-AR"), TEXT("LocalA2F-James-AR"), TEXT("LocalA2F-Claire-AR")
    };
    bConfigured = bConfigured && Character->FindComponentByClass<UACEAudioCurveSourceComponent>() &&
        LocalProviders.Contains(FaceProvider) &&
        UACEBlueprintLibrary::GetAvailableA2FProviderNames().Contains(FName(*FaceProvider));
#else
    bConfigured = false;
#endif
    if (!bConfigured) return;
    Client->OnEvent.AddUObject(this, &ADecanatAvatar::Handle);
    BodyAnimation(TEXT("SitIdleAnimation"), true, false);
}

void ADecanatAvatar::BodyAnimation(const FString& Key, bool bLoop, bool bAcknowledge)
{
    FString Path;
    GConfig->GetString(TEXT("Decanat"), *Key, Path, GGameIni);
    UAnimSequence* Animation = LoadObject<UAnimSequence>(nullptr, *Path);
    if (!Body || !Animation) { bConfigured = false; return; }
    GetWorldTimerManager().ClearTimer(BodyTimer);
    Body->PlayAnimation(Animation, bLoop);
    if (bAcknowledge) {
        const FString Idle = Key == TEXT("StandAnimation") ? TEXT("StandIdleAnimation") : TEXT("SitIdleAnimation");
        GetWorldTimerManager().SetTimer(BodyTimer, [this, Idle]() {
            BodyAnimation(Idle, true, false);
            if (Client) Client->Send(TEXT("avatar_ready"));
        }, FMath::Max(0.01f, Animation->GetPlayLength()), false);
    }
}

void ADecanatAvatar::Handle(const FString& Kind, const FString&, const TSharedPtr<FJsonObject>& Payload)
{
    if (Kind == TEXT("avatar")) {
        const FString Action = Payload->GetStringField(TEXT("action"));
        BodyAnimation(Action == TEXT("stand") ? TEXT("StandAnimation") : TEXT("SitAnimation"), false, true);
    } else if (Kind == TEXT("playback_stop") || Kind == TEXT("cleared")) {
        StopFace();
    } else if (Kind == TEXT("connection_error")) {
        StopFace();
        BodyAnimation(TEXT("SitAnimation"), false, false);
    } else if (Kind == TEXT("speech")) {
#if DECANAT_WITH_ACE
        TArray<uint8> Bytes;
        if (!FBase64::Decode(Payload->GetStringField(TEXT("audio_b64")), Bytes) || Bytes.Num() % 2) return;
        const int32 Rate = Payload->GetIntegerField(TEXT("sample_rate"));
        auto* Consumer = Character->FindComponentByClass<UACEAudioCurveSourceComponent>();
        if (!Consumer) return;
        const FName Provider(*FaceProvider);
        const uint64 Generation = ++FaceGeneration;
        TWeakObjectPtr<ADecanatAvatar> WeakThis(this);
        TSharedRef<TStrongObjectPtr<UACEAudioCurveSourceComponent>, ESPMode::ThreadSafe> HeldConsumer = MakeShared<TStrongObjectPtr<UACEAudioCurveSourceComponent>, ESPMode::ThreadSafe>(Consumer);
        // The audio device agent plays PCM. Disable ACE's audible playback in the configured character.
        AsyncTask(ENamedThreads::AnyBackgroundThreadNormalTask, [WeakThis, HeldConsumer, Generation, Provider, Rate, Bytes=MoveTemp(Bytes)]() {
            if (!WeakThis.IsValid() || WeakThis->FaceGeneration.load() != Generation) return;
            TArrayView<const int16> Samples(reinterpret_cast<const int16*>(Bytes.GetData()), Bytes.Num()/2);
            FACERuntimeModule::Get().AnimateFromAudioSamples(HeldConsumer->Get(), Samples, 1, Rate, true, {}, nullptr, Provider);
        });
#endif
    }
}

void ADecanatAvatar::StopFace()
{
    ++FaceGeneration;
#if DECANAT_WITH_ACE
    if (Character) if (auto* Consumer = Character->FindComponentByClass<UACEAudioCurveSourceComponent>()) {
        FACERuntimeModule::Get().CancelAnimationGeneration(Consumer);
        FACERuntimeModule::Get().EndAudioSamples(Consumer);
        // Cancel stops generation; StopCharacter also discards buffered animation (ACE 2.5).
        UACEBlueprintLibrary::StopCharacter(Character);
    }
#endif
}

void ADecanatAvatar::EndPlay(const EEndPlayReason::Type Reason)
{
    if (Client) Client->OnEvent.RemoveAll(this);
    GetWorldTimerManager().ClearTimer(BodyTimer);
    StopFace();
    if (Character) Character->Destroy();
    Super::EndPlay(Reason);
}
