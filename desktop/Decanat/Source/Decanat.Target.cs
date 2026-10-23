using UnrealBuildTool;
using System.Collections.Generic;

public class DecanatTarget : TargetRules
{
    public DecanatTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V5;
        ExtraModuleNames.Add("Decanat");
    }
}
