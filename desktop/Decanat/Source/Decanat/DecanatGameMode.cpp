#include "DecanatGameMode.h"
#include "DecanatClient.h"
#include "DecanatPanel.h"
#include "Camera/CameraActor.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/GameInstance.h"
#include "Kismet/GameplayStatics.h"

ADecanatGameMode::ADecanatGameMode()
{
    PlayerControllerClass = ADecanatController::StaticClass();
    DefaultPawnClass = nullptr;
}

void ADecanatController::BeginPlay()
{
    Super::BeginPlay();
    if (AActor* Camera = UGameplayStatics::GetActorOfClass(GetWorld(), ACameraActor::StaticClass())) SetViewTarget(Camera);
    bShowMouseCursor = true;
    Panel = SNew(SDecanatPanel).Client(GetGameInstance()->GetSubsystem<UDecanatClient>()).World(GetWorld());
    if (GEngine && GEngine->GameViewport) GEngine->GameViewport->AddViewportWidgetContent(Panel.ToSharedRef());
    FInputModeUIOnly Input;
    Input.SetWidgetToFocus(Panel);
    SetInputMode(Input);
}

void ADecanatController::EndPlay(const EEndPlayReason::Type Reason)
{
    if (Panel.IsValid() && GEngine && GEngine->GameViewport) GEngine->GameViewport->RemoveViewportWidgetContent(Panel.ToSharedRef());
    Panel.Reset();
    Super::EndPlay(Reason);
}
