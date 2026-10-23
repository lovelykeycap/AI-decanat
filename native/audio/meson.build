project('decanat-audio', 'c', 'cpp', version: '0.1.0', default_options: ['cpp_std=c++17', 'default_library=static', 'b_staticpic=true'])
upstream = subproject('webrtc-audio-processing-1.3')
apm = upstream.get_variable('audio_processing_dep')
shared_library('decanat_audio', 'src/apm.cpp', dependencies: apm, install: true)
