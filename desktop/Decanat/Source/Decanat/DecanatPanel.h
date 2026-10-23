#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class UDecanatClient;
class FJsonObject;
class STextBlock;
class SEditableTextBox;
class SWidgetSwitcher;

class SDecanatPanel : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SDecanatPanel) {} SLATE_ARGUMENT(UDecanatClient*, Client) SLATE_ARGUMENT(UWorld*, World) SLATE_END_ARGS()
    void Construct(const FArguments& Args);
    virtual ~SDecanatPanel();
    virtual bool SupportsKeyboardFocus() const override { return true; }
    virtual FReply OnKeyDown(const FGeometry&, const FKeyEvent&) override;
private:
    UDecanatClient* Client = nullptr;
    TWeakObjectPtr<UWorld> World;
    TSharedPtr<STextBlock> Status, Transcript, Answer, Sources, AdminStatus, JournalText, DiagnosticsText;
    TSharedPtr<SEditableTextBox> Password, ApiModel, ApiKey, PackagePath, InputDevice, OutputDevice, NewPassword, RepeatPassword;
    TSharedPtr<SWidgetSwitcher> Switcher;
    TSharedPtr<FJsonObject> Settings;
    bool bActive = false, bRuntimeReady = false, bSources = false;
    bool bIdle = true, bMaintenance = false, bError = false;
    bool bApi = false, bFallback = false;
    void Handle(const FString&, const FString&, const TSharedPtr<FJsonObject>&);
    void RefreshHealth();
    FReply Login();
    FReply SaveSettings();
    FReply SaveKey();
    FReply Upload();
    FReply ReadJournal();
    FReply Diagnostics();
    FReply ChangePassword();
};
