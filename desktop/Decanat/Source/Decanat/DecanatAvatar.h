#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include <atomic>
#include "DecanatAvatar.generated.h"

class UDecanatClient;
class USkeletalMeshComponent;
class FJsonObject;

UCLASS()
class DECANAT_API ADecanatAvatar : public AActor
{
    GENERATED_BODY()
public:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    bool IsConfigured() const { return bConfigured; }
private:
    UPROPERTY() TObjectPtr<AActor> Character;
    UPROPERTY() TObjectPtr<USkeletalMeshComponent> Body;
    UPROPERTY() TObjectPtr<UDecanatClient> Client;
    FString FaceProvider;
    bool bConfigured = false;
    std::atomic<uint64> FaceGeneration{0};
    FTimerHandle BodyTimer;
    void Handle(const FString& Kind, const FString& Turn, const TSharedPtr<FJsonObject>& Payload);
    void BodyAnimation(const FString& Key, bool bLoop, bool bAcknowledge);
    void StopFace();
};
