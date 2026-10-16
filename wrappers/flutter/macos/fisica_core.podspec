Pod::Spec.new do |s|
  s.name = 'fisica_core'
  s.version = '0.5.0'
  s.summary = 'Fisica Scale compiled core and Dart FFI'
  s.homepage = 'https://github.com/Care-Co/'
  s.license = { :type => 'Fisica Scale SDK License', :file => '../LICENSE' }
  s.author = { 'Care&Co' => 'carencoinc@carenco.kr' }
  s.source = { :path => '.' }
  s.platform = :macos, '12.0'
  s.vendored_frameworks = 'CFisica.xcframework'
  s.libraries = 'c++'
  s.user_target_xcconfig = { 'OTHER_LDFLAGS' => '$(inherited) -Wl,-u,_fisica_core_abi_version -Wl,-u,_fisica_core_analyze -Wl,-u,_fisica_core_analyze_packets -Wl,-u,_fisica_core_check_wrapper -Wl,-u,_fisica_core_command -Wl,-u,_fisica_core_cop -Wl,-u,_fisica_core_decode_packet -Wl,-u,_fisica_core_decode_response -Wl,-u,_fisica_core_dimensions -Wl,-u,_fisica_core_display_grid -Wl,-u,_fisica_core_entitlement_create -Wl,-u,_fisica_core_entitlement_destroy -Wl,-u,_fisica_core_entitlement_export -Wl,-u,_fisica_core_entitlement_import -Wl,-u,_fisica_core_entitlement_restore -Wl,-u,_fisica_core_entitlement_status -Wl,-u,_fisica_core_entitlement_text -Wl,-u,_fisica_core_error_message -Wl,-u,_fisica_core_next_packet -Wl,-u,_fisica_core_policy_json -Wl,-u,_fisica_core_preprocess -Wl,-u,_fisica_core_response_payload -Wl,-u,_fisica_core_session_action -Wl,-u,_fisica_core_session_command -Wl,-u,_fisica_core_session_create -Wl,-u,_fisica_core_session_destroy -Wl,-u,_fisica_core_session_feed -Wl,-u,_fisica_core_session_poll -Wl,-u,_fisica_core_session_tick -Wl,-u,_fisica_core_version' }
end
