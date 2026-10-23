using UnrealBuildTool;
using System.Collections.Generic;

public class DecanatEditorTarget : TargetRules
{
    public DecanatEditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.V5;
        ExtraModuleNames.Add("Decanat");
    }
}
