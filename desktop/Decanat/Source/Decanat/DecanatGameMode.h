#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/PlayerController.h"
#include "DecanatGameMode.generated.h"

UCLASS()
class DECANAT_API ADecanatController : public APlayerController
{
    GENERATED_BODY()
public:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
private:
    TSharedPtr<class SDecanatPanel> Panel;
};

UCLASS()
class DECANAT_API ADecanatGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    ADecanatGameMode();
};
